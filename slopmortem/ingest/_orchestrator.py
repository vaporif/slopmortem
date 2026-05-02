# pyright: reportAny=false
"""Ingest orchestration: sources -> enrichers -> slop -> facets -> summarize -> chunks -> qdrant.

Pipeline:

1. Per source, iterate ``Source.fetch()`` async. Per-source failures log and
   the run continues.
2. Per entry, apply enrichers in declared order. trafilatura sanitizes and
   extracts the canonical body when HTML is present; entries below the length
   floor get dropped.
3. Slop classify. ``slop_score > config.slop_threshold`` quarantines the doc:
   no qdrant point, no merge journal row, separate ``quarantine_journal``
   table, body under ``post_mortems_root/quarantine/<content_sha256>.md``.
4. Cache-warm one serial LLM call so the prompt cache is hot for fan-out.
5. Fan-out facet_extract + summarize_for_rerank under
   ``anyio.CapacityLimiter(config.ingest_concurrency)``.
6. Read-ratio probe on the first 5 fan-out responses. If
   ``cache_read / (cache_read + cache_creation) < 0.80`` emit
   :attr:`SpanEvent.CACHE_READ_RATIO_LOW`.
7. Resolve canonical id via
   :func:`slopmortem.corpus._entity_resolution.resolve_entity`. The
   ``resolver_flipped`` and ``alias_blocked`` actions short-circuit.
8. ``upsert_pending`` row, atomic raw write, deterministic merge of all raw
   sections for this canonical_id, atomic canonical write, chunk + embed,
   delete + re-upsert all chunk points, ``mark_complete`` with skip_key LAST.

Idempotency: skip_key is ``(content_hash, facet_prompt_hash,
summarize_prompt_hash, haiku_model_id, embed_model_id, chunk_strategy_version,
taxonomy_version, reliability_rank_version)``. A later ingest with a journal
row already at ``complete`` and a matching skip_key short-circuits with no
LLM, embed, or qdrant work. ``--force`` bypasses it.

Concurrency contract: every LLM and embedding call routes through the same
:class:`~slopmortem.budget.Budget` (reserve before, settle after) and the
bounded fan-out limiter for the facet+summarize batch.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Protocol, cast, runtime_checkable

from lmnr import Laminar, observe

from slopmortem.corpus import (
    extract_clean,
)
from slopmortem.ingest._slop_gate import _quarantine, classify_one
from slopmortem.ingest._warm_cache import cache_read_ratio_event, cache_warm
from slopmortem.llm import prompt_template_sha, render_prompt
from slopmortem.models import (
    CandidatePayload,
    Facets,
)
from slopmortem.tracing import SpanEvent, git_sha, mint_run_id

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from slopmortem.budget import Budget
    from slopmortem.config import Config
    from slopmortem.corpus import MergeJournal
    from slopmortem.corpus.sources import Enricher, Source
    from slopmortem.llm import EmbeddingClient, LLMClient
    from slopmortem.models import RawEntry

__all__ = [
    "INGEST_PHASE_LABELS",
    "Corpus",
    "FakeSlopClassifier",
    "HaikuSlopClassifier",
    "InMemoryCorpus",
    "IngestPhase",
    "IngestResult",
    "SlopClassifier",
    "_Point",
    "ingest",
]

# Sparse encoder shape: Callable[[text], {token_id: weight}].
type SparseEncoder = Callable[[str], dict[int, float]]

logger = logging.getLogger(__name__)

# Cap on per-entry exceptions attached as indexed attributes to the ingest span.
# Beyond this we set ``errors.truncated_count`` and stop adding attribute keys
# so a pathological run can't blow past Laminar's per-span attribute limit.
_MAX_RECORDED_ERRORS: Final[int] = 50

# Reliability rank for in-process classification. merge_text orders sections
# by this. Curated > HN > Wayback > everything else.
_RELIABILITY_RANK: Final[dict[str, int]] = {
    "curated": 0,
    "hn": 1,
    "wayback": 2,
    "crunchbase": 3,
}


@runtime_checkable
class Corpus(Protocol):
    """Narrow corpus surface ingest depends on; production is :class:`QdrantCorpus`."""

    async def upsert_chunk(self, point: object) -> None: ...

    async def has_chunks(self, canonical_id: str) -> bool: ...

    async def delete_chunks_for_canonical(self, canonical_id: str) -> None:
        # Called before a re-merge upsert.
        ...


class IngestPhase(StrEnum):
    """Phase keys used by :class:`IngestProgress`. Closed enum so typos fail at parse."""

    GATHER = "gather"
    CLASSIFY = "classify"
    CACHE_WARM = "cache_warm"
    FAN_OUT = "fan_out"
    WRITE = "write"


# Phase label map keyed on IngestPhase so any phase added above fails type-check
# at every consumer until a label is provided. Lives next to the enum so the CLI
# and the corpus recorder don't need to keep duplicate copies in sync.
INGEST_PHASE_LABELS: dict[IngestPhase, str] = {
    IngestPhase.GATHER: "Gathering entries from sources",
    IngestPhase.CLASSIFY: "Classifying / slop-filtering",
    IngestPhase.CACHE_WARM: "Warming prompt cache",
    IngestPhase.FAN_OUT: "Facets + summarize fan-out",
    IngestPhase.WRITE: "Entity-resolve / chunk / qdrant",
}


@runtime_checkable
class IngestProgress(Protocol):
    """Phase-level progress hooks for ``slopmortem ingest``.

    Default :class:`NullProgress` keeps the orchestrator decoupled from any UI
    library; the CLI wires a Rich impl. ``log`` is for neutral status lines,
    ``error`` for failures (impl paints those red).
    """

    def start_phase(self, phase: IngestPhase, total: int | None) -> None:
        """``total=None`` marks the phase indeterminate (Rich pulses; ETA blank)."""

    def advance_phase(self, phase: IngestPhase, n: int = 1) -> None: ...

    def end_phase(self, phase: IngestPhase) -> None: ...

    def log(self, message: str) -> None: ...

    def error(self, phase: IngestPhase, message: str) -> None: ...


class NullProgress:
    """No-op :class:`IngestProgress` used when no display surface is attached."""

    def start_phase(self, phase: IngestPhase, total: int | None) -> None: ...

    def advance_phase(self, phase: IngestPhase, n: int = 1) -> None: ...

    def end_phase(self, phase: IngestPhase) -> None: ...

    def log(self, message: str) -> None: ...

    def error(self, phase: IngestPhase, message: str) -> None: ...


@runtime_checkable
class SlopClassifier(Protocol):
    """Score a document for LLM-generated-text likelihood; ``> threshold`` quarantines."""

    async def score(self, text: str) -> float: ...


@dataclass
class _Point:
    """Tiny stand-in for a Qdrant point. Production builds qdrant_client.models.PointStruct."""

    id: str
    vector: dict[str, object]
    payload: dict[str, object]


@dataclass
class InMemoryCorpus:
    """In-memory :class:`Corpus` impl used by ingest tests; not used in production."""

    points: list[_Point] = field(default_factory=list)

    async def upsert_chunk(self, point: object) -> None:
        if not isinstance(point, _Point):
            msg = f"InMemoryCorpus expects _Point, got {type(point).__name__}"
            raise TypeError(msg)
        self.points.append(point)

    async def has_chunks(self, canonical_id: str) -> bool:
        return any(p.payload.get("canonical_id") == canonical_id for p in self.points)

    async def delete_chunks_for_canonical(self, canonical_id: str) -> None:
        self.points = [p for p in self.points if p.payload.get("canonical_id") != canonical_id]


@dataclass
class FakeSlopClassifier:
    """Deterministic test :class:`SlopClassifier`. ``scores`` overrides per text key prefix."""

    default_score: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)

    async def score(self, text: str) -> float:
        for key, val in self.scores.items():
            if text.startswith(key) or key in text:
                return val
        return self.default_score


@dataclass
class HaikuSlopClassifier:
    """Asks Haiku whether a text describes a dead company.

    Only runs on open-corpus sources (HN). Pre-vetted sources bypass slop in
    :func:`ingest` since their bodies are human-reviewed obituaries or
    Wayback'd live-era marketing pages.

    Returns 0.0 when Haiku says yes, else 1.0 (above ``slop_threshold=0.7``,
    so the entry quarantines).

    ``char_limit=6000`` is sized so the demise narrative (often deep in the
    body for older companies like Sun Microsystems or WeWork) falls inside
    the window. Tighter 1500-char caps cause false-negative quarantines on
    long obituaries.
    """

    llm: LLMClient
    model: str
    char_limit: int = 6000
    max_tokens: int | None = None

    async def score(self, text: str) -> float:
        snippet = text[: self.char_limit]
        prompt = render_prompt("slop_judge", text=snippet)
        result = await self.llm.complete(
            prompt,
            model=self.model,
            cache=False,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "SlopJudge",
                    "schema": {
                        "type": "object",
                        "properties": {"is_dead_company": {"type": "boolean"}},
                        "required": ["is_dead_company"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                },
            },
            extra_body={"prompt_template_sha": prompt_template_sha("slop_judge")},
            max_tokens=self.max_tokens,
        )
        try:
            parsed: object = json.loads(result.text)
        except json.JSONDecodeError:
            # Conservative on parse failure: keep the entry rather than silently drop.
            return 0.0
        if not isinstance(parsed, dict):
            return 1.0
        is_dead = cast("dict[str, object]", parsed).get("is_dead_company")
        return 0.0 if is_dead is True else 1.0


@dataclass
class IngestResult:
    """Roll-up of one ingest run for the operator and CI."""

    seen: int = 0
    processed: int = 0
    quarantined: int = 0
    skipped: int = 0
    skipped_empty: int = 0
    failed: int = 0
    errors: int = 0
    source_failures: int = 0
    would_process: int = 0  # populated when dry_run=True
    dry_run: bool = False
    cache_warmed: bool = False
    cache_creation_tokens_warm: int = 0
    span_events: list[str] = field(default_factory=list)


def _text_id_for(canonical_id: str) -> str:  # pyright: ignore[reportUnusedFunction]  -- imported by _fan_out.py and _journal_writes.py
    return hashlib.sha256(canonical_id.encode("utf-8")).hexdigest()[:16]


def _reliability_for(source: str) -> int:  # pyright: ignore[reportUnusedFunction]  -- imported by _journal_writes.py
    return _RELIABILITY_RANK.get(source, 9)


def _skip_key(  # noqa: PLR0913 - the contract tuple is wide  # pyright: ignore[reportUnusedFunction]  -- imported by _journal_writes.py
    *,
    content_hash: str,
    facet_sha: str,
    summarize_sha: str,
    haiku_model_id: str,
    embed_model_id: str,
    chunk_strategy: str,
    taxonomy_version: str,
    reliability_rank_version: str,
) -> str:
    raw = (
        f"{content_hash}|{facet_sha}|{summarize_sha}|"
        f"{haiku_model_id}|{embed_model_id}|{chunk_strategy}|"
        f"{taxonomy_version}|{reliability_rank_version}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Clip *text* to at most *max_tokens* under cl100k_base.

    Anthropic's tokenizer isn't published; cl100k_base (GPT-4) is a fine proxy
    for cost control — the two agree to within ~10% on English prose, well
    inside the truncation budget's headroom.
    """
    if max_tokens <= 0:
        return text
    import tiktoken  # noqa: PLC0415 - heavy dep; lazy

    enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return enc.decode(tokens[:max_tokens])


def _entry_summary_text(entry: RawEntry, *, max_tokens: int) -> str:
    """Return the entry's body text for slop / facet / summarize.

    Clipped to *max_tokens* (cl100k_base proxy) to bound LLM input cost on
    long-tail articles. Wikipedia entries especially can be 60KB+ after
    trafilatura, but the demise narrative is almost always in the lead and
    first major body section.
    """
    if entry.markdown_text:
        return _truncate_to_tokens(entry.markdown_text, max_tokens)
    if entry.raw_html:
        return _truncate_to_tokens(extract_clean(entry.raw_html), max_tokens)
    return ""


async def _enrich_pipeline(entry: RawEntry, enrichers: Sequence[Enricher]) -> RawEntry:
    cur = entry
    for e in enrichers:
        cur = await e.enrich(cur)
    return cur


async def _gather_entries(
    sources: Sequence[Source],
    *,
    span_events: list[str],
    limit: int | None = None,
    progress: IngestProgress | None = None,
) -> tuple[list[RawEntry], int]:
    """Collect entries from every source. Per-source failures are logged and counted.

    When *limit* is set, gather stops as soon as ``len(out) >= limit``. Sources
    beyond the cap aren't started, and an in-progress source breaks out of its
    async iterator. ``--limit`` is a real fast-path knob for smoke tests, not
    just a post-gather slice.

    ``progress`` (when provided) gets one ``advance_phase(GATHER)`` per entry so
    Rich's speed sampler can extrapolate an ETA when ``limit`` gives the bar a
    known total.
    """
    out: list[RawEntry] = []
    failures = 0
    bar = progress or NullProgress()
    for src in sources:
        if limit is not None and len(out) >= limit:
            break
        try:
            iterable = src.fetch()
            async for entry in iterable:
                out.append(entry)
                bar.advance_phase(IngestPhase.GATHER)
                if limit is not None and len(out) >= limit:
                    break
        except Exception as exc:  # noqa: BLE001 - never abort the run on a per-source failure.
            logger.warning(
                "ingest: source %r failed: %s",
                type(src).__name__,
                exc,
            )
            span_events.append(SpanEvent.SOURCE_FETCH_FAILED.value)
            failures += 1
    return out, failures


def _build_payload(  # noqa: PLR0913 - payload assembly takes every store-time field  # pyright: ignore[reportUnusedFunction]  -- imported by _journal_writes.py
    *,
    facets: Facets,
    summary: str,
    body: str,
    slop_score: float,
    sources_seen: list[str],
    provenance_id: str,
    text_id: str,
    name: str,
    provenance: str,
) -> CandidatePayload:
    """Build the :class:`CandidatePayload` written into every chunk point."""
    founding_year = facets.founding_year
    failure_year = facets.failure_year
    return CandidatePayload(
        name=name,
        summary=summary,
        body=body,
        facets=facets,
        founding_date=None if founding_year is None else _date_from_year(founding_year),
        failure_date=None if failure_year is None else _date_from_year(failure_year),
        founding_date_unknown=founding_year is None,
        failure_date_unknown=failure_year is None,
        provenance="curated_real" if provenance == "curated" else "scraped",
        slop_score=slop_score,
        sources=sources_seen,
        provenance_id=provenance_id,
        text_id=text_id,
    )


def _date_from_year(year: int):  # noqa: ANN202 - narrow internal helper
    from datetime import date  # noqa: PLC0415

    return date(year, 1, 1)


from slopmortem.ingest._fan_out import (  # noqa: E402  -- runtime circular import; placed after the names _fan_out imports back from this module
    _facet_summarize_fanout,
    _FanoutResult,
)
from slopmortem.ingest._journal_writes import (  # noqa: E402  -- runtime circular import; placed after the helpers _journal_writes imports back from this module
    ProcessOutcome,
    _process_entry,
)


@observe(
    name="ingest",
    ignore_inputs=[
        "sources",
        "enrichers",
        "journal",
        "corpus",
        "llm",
        "embed_client",
        "budget",
        "slop_classifier",
        "sparse_encoder",
        "progress",
    ],
)
async def ingest(  # noqa: PLR0913, C901, PLR0912, PLR0915 - orchestration takes every dependency.
    *,
    sources: Sequence[Source],
    enrichers: Sequence[Enricher],
    journal: MergeJournal,
    corpus: Corpus,
    llm: LLMClient,
    embed_client: EmbeddingClient,
    budget: Budget,  # noqa: ARG001 - consumed by LLM/embed clients at construction time
    slop_classifier: SlopClassifier,
    config: Config,
    post_mortems_root: Path,
    dry_run: bool = False,
    force: bool = False,
    sparse_encoder: SparseEncoder | None = None,
    limit: int | None = None,
    progress: IngestProgress | None = None,
) -> IngestResult:
    """Run one full ingest pass and return the aggregated :class:`IngestResult`.

    Per-entry and per-source failures log and continue; only budget exhaustion
    truncates the run. ``dry_run`` counts entries without writing journal,
    disk, or qdrant. ``force`` bypasses the skip_key short-circuit.
    ``sparse_encoder=None`` lazy-loads the production fastembed model; tests
    pass a no-op stub to dodge the ~150 MB ONNX download.
    """
    result = IngestResult(dry_run=dry_run)
    progress = progress or NullProgress()

    # Closure so every early-return path can drain the events; the list itself
    # stays in result for tests and the CLI renderer.
    def _emit_collected_events() -> None:
        if not Laminar.is_initialized():
            return
        for name in result.span_events:
            Laminar.event(name=name)

    # Without per-entry attributes, swallowed exceptions only show up in stderr
    # — the parent span returns OK and INGEST_ENTRY_FAILED carries no payload.
    def _record_error(entry_label: str, exc: BaseException) -> None:
        if not Laminar.is_initialized():
            return
        idx = result.errors
        if idx >= _MAX_RECORDED_ERRORS:
            Laminar.set_span_attributes({"errors.truncated_count": idx - _MAX_RECORDED_ERRORS + 1})
            return
        Laminar.set_span_attributes(
            {
                f"errors.{idx}.entry": entry_label,
                f"errors.{idx}.exception_type": type(exc).__name__,
                f"errors.{idx}.message": str(exc)[:500],
            }
        )

    if Laminar.is_initialized():
        Laminar.set_span_attributes(
            {
                "run.id": mint_run_id(),
                "run.kind": "ingest",
                "run.git_sha": git_sha() or "",
                "run.dry_run": dry_run,
                "run.force": force,
                "run.limit": limit if limit is not None else 0,
                "config.taxonomy_version": config.taxonomy_version,
                "config.reliability_rank_version": config.reliability_rank_version,
                "config.slop_threshold": config.slop_threshold,
                "config.model_facet": config.model_facet,
                "config.model_summarize": config.model_summarize,
            }
        )

    # Default sparse encoder: BM25 via fastembed. Tests stub it with a
    # dict-returning lambda so the ONNX model never loads under pytest.
    if sparse_encoder is None:
        from slopmortem.corpus._embed_sparse import encode as _encode_sparse  # noqa: PLC0415

        sparse_encoder = _encode_sparse

    # When ``--limit`` is set, ``total=limit`` gives Rich a real denominator
    # so the ETA column works. Without it, the count isn't known up front,
    # so pass ``None`` (indeterminate, pulsing bar) rather than lying with 0.
    progress.start_phase(IngestPhase.GATHER, total=limit)
    entries, source_failures = await _gather_entries(
        sources, span_events=result.span_events, limit=limit, progress=progress
    )
    progress.end_phase(IngestPhase.GATHER)
    progress.log(f"gathered {len(entries)} entries from {len(sources)} sources")
    result.source_failures = source_failures

    progress.start_phase(IngestPhase.CLASSIFY, total=len(entries))
    keepers: list[tuple[RawEntry, str]] = []  # (entry, body) post-extract.
    for entry in entries:
        result.seen += 1
        try:
            enriched = await _enrich_pipeline(entry, enrichers)
        except Exception as exc:  # noqa: BLE001 - per-entry isolation.
            logger.warning("ingest: enricher failed for %r: %s", entry.source_id, exc)
            progress.error(IngestPhase.CLASSIFY, f"enricher failed for {entry.source_id}: {exc}")
            _record_error(f"{entry.source}:{entry.source_id}", exc)
            result.errors += 1
            result.span_events.append(SpanEvent.INGEST_ENTRY_FAILED.value)
            progress.advance_phase(IngestPhase.CLASSIFY)
            continue

        body = _entry_summary_text(enriched, max_tokens=config.max_doc_tokens)
        if not body:
            result.skipped += 1
            progress.advance_phase(IngestPhase.CLASSIFY)
            continue

        slop_score = await classify_one(
            entry=enriched,
            body=body,
            slop_classifier=slop_classifier,
            on_error=lambda exc: progress.error(
                IngestPhase.CLASSIFY, f"slop classifier failed: {exc}"
            ),
        )

        if slop_score > config.slop_threshold:
            if not dry_run:
                await _quarantine(
                    journal=journal,
                    entry=enriched,
                    body=body,
                    slop_score=slop_score,
                    post_mortems_root=post_mortems_root,
                )
            result.quarantined += 1
            result.span_events.append(SpanEvent.SLOP_QUARANTINED.value)
            progress.advance_phase(IngestPhase.CLASSIFY)
            continue

        keepers.append((enriched, body))
        progress.advance_phase(IngestPhase.CLASSIFY)
    progress.end_phase(IngestPhase.CLASSIFY)
    quarantined = result.quarantined
    skipped = result.skipped
    progress.log(f"classified: {len(keepers)} kept, {quarantined} quarantined, {skipped} skipped")

    if dry_run:
        result.would_process = len(keepers)
        _emit_collected_events()
        return result

    if not keepers:
        _emit_collected_events()
        return result

    progress.start_phase(IngestPhase.CACHE_WARM, total=1)
    warmed, warm_creation, warm_events = await cache_warm(
        llm=llm,
        model=config.model_summarize,
        seed_text=keepers[0][1][:1000],
        max_tokens=config.max_tokens_summarize,
    )
    progress.advance_phase(IngestPhase.CACHE_WARM)
    progress.end_phase(IngestPhase.CACHE_WARM)
    result.cache_warmed = warmed
    result.cache_creation_tokens_warm = warm_creation
    result.span_events.extend(warm_events)

    progress.start_phase(IngestPhase.FAN_OUT, total=len(keepers))
    fanout = await _facet_summarize_fanout(keepers, llm=llm, config=config, progress=progress)
    progress.end_phase(IngestPhase.FAN_OUT)

    ratio_event = cache_read_ratio_event([r for r in fanout if isinstance(r, _FanoutResult)])
    if ratio_event:
        result.span_events.append(ratio_event)

    progress.start_phase(IngestPhase.WRITE, total=len(keepers))
    for (entry, body), fan in zip(keepers, fanout, strict=True):
        if isinstance(fan, BaseException):
            logger.warning(
                "ingest: fan-out failed for %s:%s: %s", entry.source, entry.source_id, fan
            )
            progress.error(
                IngestPhase.FAN_OUT,
                f"fan-out failed for {entry.source}:{entry.source_id}: {fan}",
            )
            _record_error(f"{entry.source}:{entry.source_id}", fan)
            result.errors += 1
            result.span_events.append(SpanEvent.INGEST_ENTRY_FAILED.value)
            progress.advance_phase(IngestPhase.WRITE)
            continue
        try:
            outcome = await _process_entry(
                entry,
                body=body,
                fan=fan,
                journal=journal,
                corpus=corpus,
                embed_client=embed_client,
                llm=llm,
                config=config,
                post_mortems_root=post_mortems_root,
                slop_score=0.0,  # we already filtered slop above
                force=force,
                span_events=result.span_events,
                sparse_encoder=sparse_encoder,
            )
        except Exception as exc:  # noqa: BLE001 - per-entry isolation; run continues.
            logger.warning(
                "ingest: write phase failed for %s:%s: %s",
                entry.source,
                entry.source_id,
                exc,
            )
            progress.error(
                IngestPhase.WRITE,
                f"write phase failed for {entry.source}:{entry.source_id}: {exc}",
            )
            _record_error(f"{entry.source}:{entry.source_id}", exc)
            result.errors += 1
            result.span_events.append(SpanEvent.INGEST_ENTRY_FAILED.value)
            progress.advance_phase(IngestPhase.WRITE)
            continue
        except BaseException as exc:
            # CancelledError / SystemExit / etc; ``except Exception`` above misses
            # these. Surface what's escaping (which entry, what type) via the
            # progress error channel before letting it propagate, so the run
            # terminates loud rather than silent.
            progress.error(
                IngestPhase.WRITE,
                f"FATAL {type(exc).__name__} on {entry.source}:{entry.source_id}: {exc}",
            )
            raise
        match outcome:
            case ProcessOutcome.PROCESSED:
                result.processed += 1
            case ProcessOutcome.SKIPPED:
                result.skipped += 1
            case ProcessOutcome.SKIPPED_EMPTY:
                result.skipped_empty += 1
            case ProcessOutcome.FAILED:
                result.failed += 1
        progress.advance_phase(IngestPhase.WRITE)
    progress.end_phase(IngestPhase.WRITE)

    _emit_collected_events()
    return result
