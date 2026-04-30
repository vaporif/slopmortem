# pyright: reportAny=false
"""Ingest orchestration: sources -> enrichers -> slop -> facets -> summarize -> chunks -> qdrant.

Pipeline (per spec lines 478-606):

1. Per source, iterate ``Source.fetch()`` async; per-source failures are logged
   and the run continues (spec line 606).
2. Per entry, apply enrichers in declared order; trafilatura sanitizes and
   extracts the canonical body if HTML is present; entries below the length
   floor are dropped.
3. Slop classify; ``slop_score > config.slop_threshold`` quarantines the doc
   (no qdrant point, no merge journal row — separate ``quarantine_journal``
   table; body under ``post_mortems_root/quarantine/<content_sha256>.md``).
4. Cache-warm one serial LLM call so the prompt cache is hot before fan-out.
5. Fan-out facet_extract + summarize_for_rerank under a
   ``anyio.CapacityLimiter(config.ingest_concurrency)``.
6. Read-ratio probe on the first 5 fan-out responses; if
   ``cache_read / (cache_read + cache_creation) < 0.80`` emit
   :attr:`SpanEvent.CACHE_READ_RATIO_LOW`.
7. Resolve canonical id via :func:`slopmortem.corpus.entity_resolution.resolve_entity`;
   ``resolver_flipped`` and ``alias_blocked`` actions short-circuit.
8. ``upsert_pending`` row, atomic raw write, deterministic merge of all raw
   sections for this canonical_id, atomic canonical write, chunk + embed,
   delete + re-upsert all chunk points, ``mark_complete`` with skip_key LAST.

Idempotency: skip_key is the spec-line-579 tuple
``(content_hash, facet_prompt_hash, summarize_prompt_hash, haiku_model_id,
embed_model_id, chunk_strategy_version, taxonomy_version,
reliability_rank_version)``. A later ingest with a journal row already at
``complete`` and matching skip_key short-circuits without LLM, embed, or
qdrant work. ``--force`` bypasses the short-circuit.

Concurrency contract: every LLM/embedding call routes through the same
:class:`~slopmortem.budget.Budget` (reserve before, settle after) and
through the bounded fan-out limiter for the facet+summarize batch.
"""

import contextlib
import hashlib
import json
import logging
import secrets
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Protocol, cast, runtime_checkable

import anyio
from anyio import to_thread

from slopmortem._time import utcnow_iso
from slopmortem.concurrency import gather_resilient
from slopmortem.corpus.chunk import CHUNK_STRATEGY_VERSION, chunk_markdown
from slopmortem.corpus.disk import write_canonical_atomic, write_raw_atomic
from slopmortem.corpus.entity_resolution import resolve_entity
from slopmortem.corpus.extract import extract_clean
from slopmortem.corpus.merge_text import Section, combined_hash, combined_text
from slopmortem.corpus.paths import safe_path
from slopmortem.llm.prompts import prompt_template_sha, render_prompt
from slopmortem.models import (
    CandidatePayload,
    Facets,
)
from slopmortem.tracing.events import SpanEvent

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from slopmortem.budget import Budget
    from slopmortem.config import Config
    from slopmortem.corpus.merge import MergeJournal
    from slopmortem.corpus.sources.base import Enricher, Source
    from slopmortem.llm.client import LLMClient
    from slopmortem.llm.embedding_client import EmbeddingClient
    from slopmortem.models import RawEntry

# Sparse encoder shape — Callable[[text], {token_id: weight}].
type SparseEncoder = Callable[[str], dict[int, float]]

logger = logging.getLogger(__name__)

# Spec line 205: read-ratio threshold over the first N fan-out responses.
_CACHE_READ_RATIO_THRESHOLD: Final[float] = 0.80
_CACHE_READ_RATIO_PROBE_N: Final[int] = 5

# Reliability rank for in-process classification. merge_text orders sections
# by this. Curated > HN > Wayback > everything else.
_RELIABILITY_RANK: Final[dict[str, int]] = {
    "curated": 0,
    "hn": 1,
    "wayback": 2,
    "crunchbase": 3,
}

# Sources whose entries are pre-filtered to "confirmed dead company" upstream
# of slopmortem itself: curated YAML is human-reviewed, crunchbase_csv is
# filtered to status=closed. Running the LLM dead-company classifier on these
# is wasted spend AND systematically misclassifies — Wayback'd Crunchbase
# homepages are pre-death marketing copy, not death narratives. Skip slop for
# them entirely; HN and any future open-corpus sources still go through it.
_PRE_VETTED_SOURCES: Final[frozenset[str]] = frozenset({"curated", "crunchbase_csv"})


# ─── Protocols ─────────────────────────────────────────────────────────────────


@runtime_checkable
class Corpus(Protocol):
    """Narrow corpus surface ingest depends on; production is :class:`QdrantCorpus`."""

    async def upsert_chunk(self, point: object) -> None:
        """Upsert one chunk point into the underlying store."""
        ...

    async def has_chunks(self, canonical_id: str) -> bool:
        """Return whether at least one chunk exists for *canonical_id*."""
        ...

    async def delete_chunks_for_canonical(self, canonical_id: str) -> None:
        """Drop every chunk point for *canonical_id*, used before a re-merge upsert."""
        ...


class IngestPhase(StrEnum):
    """Closed set of phase keys used by :class:`IngestProgress`.

    StrEnum (rather than bare strings) gives us closed-set typing, IDE
    autocomplete, and exhaustiveness checks — typos like ``"fanout"`` for
    ``"fan_out"`` fail at parse time instead of silently no-op'ing.
    """

    GATHER = "gather"
    CLASSIFY = "classify"
    CACHE_WARM = "cache_warm"
    FAN_OUT = "fan_out"
    WRITE = "write"


@runtime_checkable
class IngestProgress(Protocol):
    """Phase-level progress hooks for ``slopmortem ingest``.

    Methods are no-op-safe: a default :class:`NullProgress` keeps the
    orchestrator decoupled from any specific UI library, while the CLI
    wires a Rich-based implementation. ``log`` is for neutral status lines
    (cache-warm result, classification summary); ``error`` is reserved for
    actual failures and the implementation paints those red.
    """

    def start_phase(self, phase: IngestPhase, total: int) -> None:
        """Announce the start of *phase* with an expected ``total`` of advances."""
        ...

    def advance_phase(self, phase: IngestPhase, n: int = 1) -> None:
        """Advance the bar for *phase* by ``n``."""
        ...

    def end_phase(self, phase: IngestPhase) -> None:
        """Mark *phase* as complete; the bar fills to its declared total."""
        ...

    def log(self, message: str) -> None:
        """Emit a one-off status line alongside the progress display."""
        ...

    def error(self, phase: IngestPhase, message: str) -> None:
        """Record an error against *phase* and surface it as a red status line."""
        ...


@dataclass
class NullProgress:
    """No-op :class:`IngestProgress` used when no display surface is attached."""

    def start_phase(self, phase: IngestPhase, total: int) -> None:  # noqa: ARG002
        """No-op."""

    def advance_phase(self, phase: IngestPhase, n: int = 1) -> None:  # noqa: ARG002
        """No-op."""

    def end_phase(self, phase: IngestPhase) -> None:  # noqa: ARG002
        """No-op."""

    def log(self, message: str) -> None:  # noqa: ARG002
        """No-op."""

    def error(self, phase: IngestPhase, message: str) -> None:  # noqa: ARG002
        """No-op."""


@runtime_checkable
class SlopClassifier(Protocol):
    """Score a document for LLM-generated-text likelihood; ``> threshold`` quarantines."""

    async def score(self, text: str) -> float:
        """Return the slop score in ``[0, 1]`` for *text*."""
        ...


# ─── Test doubles (production stays in qdrant_store + classifier impl below) ───


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
        """Append the point to the in-memory list. Always succeeds."""
        if not isinstance(point, _Point):
            msg = f"InMemoryCorpus expects _Point, got {type(point).__name__}"
            raise TypeError(msg)
        self.points.append(point)

    async def has_chunks(self, canonical_id: str) -> bool:
        """Return whether any point has the given parent canonical id."""
        return any(p.payload.get("canonical_id") == canonical_id for p in self.points)

    async def delete_chunks_for_canonical(self, canonical_id: str) -> None:
        """Drop every point whose payload references *canonical_id*."""
        self.points = [p for p in self.points if p.payload.get("canonical_id") != canonical_id]


@dataclass
class FakeSlopClassifier:
    """Deterministic test :class:`SlopClassifier`. ``scores`` overrides per text key prefix."""

    default_score: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)

    async def score(self, text: str) -> float:
        """Look up ``scores`` by exact match on the first 200 chars; fall back to default."""
        for key, val in self.scores.items():
            if text.startswith(key) or key in text:
                return val
        return self.default_score


@dataclass
class HaikuSlopClassifier:
    """Slop classifier that asks Haiku to judge dead-company relevance.

    Only runs on open-corpus sources (HN). Pre-vetted sources (curated YAML,
    Crunchbase CSV) bypass slop in :func:`ingest` because their bodies are
    either human-reviewed obituaries or Wayback'd live-era marketing pages —
    neither benefits from a death-narrative LLM check.

    One LLM call per text. Returns 0.0 when Haiku says the text describes a
    specific dead company, else 1.0 (which exceeds ``slop_threshold=0.7``
    and quarantines the entry).

    ``char_limit=6000`` is sized so the demise narrative — which can sit deep
    in the body for older companies like Sun Microsystems or WeWork — falls
    inside the window. ~$6 extra per full HN pass vs a tighter 1500-char cap,
    and avoids false-negative quarantines on long obituaries.
    """

    llm: LLMClient
    model: str
    char_limit: int = 6000

    async def score(self, text: str) -> float:
        """Ask Haiku whether *text* describes a dead company; map to 0.0 or 1.0."""
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


# ─── Result type ───────────────────────────────────────────────────────────────


@dataclass
class IngestResult:
    """Roll-up of one ingest run for the operator and CI."""

    seen: int = 0
    processed: int = 0
    quarantined: int = 0
    skipped: int = 0
    errors: int = 0
    source_failures: int = 0
    would_process: int = 0  # populated when dry_run=True
    dry_run: bool = False
    cache_warmed: bool = False
    cache_creation_tokens_warm: int = 0
    span_events: list[str] = field(default_factory=list)


# ─── Internal helpers ──────────────────────────────────────────────────────────


def _content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text_id_for(canonical_id: str) -> str:
    return hashlib.sha256(canonical_id.encode("utf-8")).hexdigest()[:16]


def _reliability_for(source: str) -> int:
    return _RELIABILITY_RANK.get(source, 9)


def _skip_key(  # noqa: PLR0913 — the spec-defined tuple is wide
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
    """Clip *text* so it encodes to at most *max_tokens* under cl100k_base.

    Anthropic's tokenizer isn't published; cl100k_base (used by GPT-4) is a
    good proxy for cost-control purposes — Anthropic and OpenAI tokenizers
    agree to within ~10% on English prose, which is well inside the
    headroom for a truncation budget.
    """
    if max_tokens <= 0:
        return text
    import tiktoken  # noqa: PLC0415 — heavy dep; lazy

    enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return enc.decode(tokens[:max_tokens])


def _entry_summary_text(entry: RawEntry, *, max_tokens: int) -> str:
    """Return the entry's body text for slop+facet+summarize stages.

    Body is clipped to *max_tokens* tokens (cl100k_base proxy) to bound
    LLM input cost on long-tail articles. Wikipedia articles especially
    can be 60KB+ of plain text after trafilatura; the demise narrative
    is almost always in the lead and first major body section.
    """
    if entry.markdown_text:
        return _truncate_to_tokens(entry.markdown_text, max_tokens)
    if entry.raw_html:
        return _truncate_to_tokens(extract_clean(entry.raw_html), max_tokens)
    return ""


async def _quarantine(
    *,
    journal: MergeJournal,
    entry: RawEntry,
    body: str,
    slop_score: float,
    post_mortems_root: Path,
) -> None:
    """Write quarantine markdown + journal row for a slop-flagged entry."""
    sha = _content_sha256(body)
    path = safe_path(post_mortems_root, kind="quarantine", content_sha256=sha)

    def _write_sync() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f"{path.suffix}.{secrets.token_hex(8)}.tmp")
        try:
            tmp.write_text(body, encoding="utf-8")
            tmp.replace(path)
        finally:
            if tmp.exists():
                tmp.unlink()

    await to_thread.run_sync(_write_sync)
    await journal.write_quarantine(
        content_sha256=sha,
        source=entry.source,
        source_id=entry.source_id,
        reason="slop_classifier",
        slop_score=slop_score,
    )


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
) -> tuple[list[RawEntry], int]:
    """Collect entries from every source. Per-source failures are logged + counted.

    When *limit* is set, gather stops as soon as ``len(out) >= limit`` —
    sources beyond the cap aren't started, and an in-progress source
    breaks out of its async iterator. This makes ``--limit`` a true
    fast-path knob for smoke tests instead of just a post-gather slice.
    """
    out: list[RawEntry] = []
    failures = 0
    for src in sources:
        if limit is not None and len(out) >= limit:
            break
        try:
            iterable = src.fetch()
            async for entry in iterable:
                out.append(entry)
                if limit is not None and len(out) >= limit:
                    break
        except Exception as exc:  # noqa: BLE001 — spec line 606: never abort the run.
            logger.warning(
                "ingest: source %r failed: %s",
                type(src).__name__,
                exc,
            )
            span_events.append(SpanEvent.SOURCE_FETCH_FAILED.value)
            failures += 1
    return out, failures


# ─── Stage helpers (facets, summarize, embed, chunk-and-upsert) ────────────────


async def _facet_call(text: str, *, llm: LLMClient, model: str | None) -> Facets:
    # Delegates to the dedicated stage; strict-mode validation propagates as
    # ``ValidationError`` to the per-entry isolator at line ~840.
    from slopmortem.stages.facet_extract import extract_facets  # noqa: PLC0415

    return await extract_facets(text, llm, model)


async def _summarize_call(text: str, *, llm: LLMClient, model: str | None) -> tuple[str, int, int]:
    """Return ``(summary, cache_read, cache_creation)``."""
    from slopmortem.corpus.summarize import summarize_for_rerank  # noqa: PLC0415

    # We need the raw response cache stats; render + call directly here.
    prompt = render_prompt("summarize", body=text, source_id="")
    res = await llm.complete(
        prompt,
        model=model,
        cache=True,
        extra_body={"prompt_template_sha": prompt_template_sha("summarize")},
    )
    summary = res.text.strip()
    _ = summarize_for_rerank  # imported to keep public surface alive in the module
    return summary, res.cache_read_tokens or 0, res.cache_creation_tokens or 0


async def _cache_warm(
    *,
    llm: LLMClient,
    model: str | None,
    seed_text: str,
) -> tuple[bool, int, list[str]]:
    """Run one serial summarize call to warm the prompt cache.

    Returns ``(warmed, cache_creation_tokens, span_events)``. ``warmed`` is
    True if the call returned ``cache_creation_tokens > 0`` (cache was created).
    """
    span: list[str] = []
    try:
        prompt = render_prompt("summarize", body=seed_text, source_id="warm")
        res = await llm.complete(
            prompt,
            model=model,
            cache=True,
            extra_body={"prompt_template_sha": prompt_template_sha("summarize")},
        )
        creation = res.cache_creation_tokens or 0
        if creation == 0:
            span.append(SpanEvent.CACHE_WARM_FAILED.value)
            return False, 0, span
    except Exception as exc:  # noqa: BLE001 — warming is best-effort
        logger.warning("ingest: cache warm failed: %s", exc)
        span.append(SpanEvent.CACHE_WARM_FAILED.value)
        return False, 0, span
    else:
        return True, creation, span


def _build_payload(  # noqa: PLR0913 — payload assembly takes every store-time field
    *,
    facets: Facets,
    summary: str,
    body: str,
    slop_score: float,
    sources_seen: list[str],
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
        text_id=text_id,
    )


def _date_from_year(year: int):  # noqa: ANN202 — narrow internal helper
    from datetime import date  # noqa: PLC0415

    return date(year, 1, 1)


async def _embed_and_upsert(  # noqa: PLR0913 — every dependency is required at the chunk site
    *,
    canonical_id: str,
    body: str,
    payload: CandidatePayload,
    corpus: Corpus,
    embed_client: EmbeddingClient,
    embed_model_id: str,
    budget: Budget,
    sparse_encoder: SparseEncoder,
) -> int:
    """Chunk, embed dense + sparse, and upsert one point per chunk. Returns count."""
    chunks = chunk_markdown(body, parent_canonical_id=canonical_id)
    if not chunks:
        return 0
    texts = [c.text for c in chunks]
    embed_result = await embed_client.embed(texts, model=embed_model_id)
    _ = budget  # embedding clients settle budget internally; we keep the param to
    # surface the contract here even if we don't reserve again.
    text_id = _text_id_for(canonical_id)
    for c, vec in zip(chunks, embed_result.vectors, strict=True):
        sparse = sparse_encoder(c.text)
        point_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{canonical_id}:{c.chunk_idx}").hex
        payload_dict = payload.model_dump(mode="json")
        payload_dict["canonical_id"] = canonical_id
        payload_dict["chunk_idx"] = c.chunk_idx
        payload_dict["text_id"] = text_id
        point = _Point(
            id=point_id,
            vector={"dense": vec, "sparse": sparse},
            payload=payload_dict,
        )
        await corpus.upsert_chunk(point)
    return len(chunks)


# ─── Per-entry pipeline ────────────────────────────────────────────────────────


@dataclass
class _FanoutResult:
    facets: Facets
    summary: str
    cache_read: int
    cache_creation: int


async def _facet_summarize_fanout(
    entries: Sequence[tuple[RawEntry, str]],
    *,
    llm: LLMClient,
    config: Config,
    progress: IngestProgress | None = None,
) -> list[_FanoutResult | BaseException]:
    """Run facet+summarize concurrently under ``ingest_concurrency`` capacity.

    Returns one :class:`_FanoutResult` per entry, in order, or the exception
    that aborted that entry's pipeline. The limiter bounds LLM calls in
    flight. Facet and summarize for the same entry run sequentially so two
    LLM calls never share one limiter slot.
    """
    limiter = anyio.CapacityLimiter(config.ingest_concurrency)
    bar = progress or NullProgress()

    async def _run(text: str) -> _FanoutResult:
        async with limiter:
            facets = await _facet_call(text, llm=llm, model=config.model_facet)
        async with limiter:
            summary, cr, cc = await _summarize_call(text, llm=llm, model=config.model_summarize)
        bar.advance_phase(IngestPhase.FAN_OUT)
        return _FanoutResult(facets=facets, summary=summary, cache_read=cr, cache_creation=cc)

    return await gather_resilient(*(_run(text) for _, text in entries))


async def _process_entry(  # noqa: PLR0913 — orchestration density is the contract
    entry: RawEntry,
    *,
    body: str,
    fan: _FanoutResult,
    journal: MergeJournal,
    corpus: Corpus,
    embed_client: EmbeddingClient,
    llm: LLMClient,
    config: Config,
    post_mortems_root: Path,
    slop_score: float,
    force: bool,
    span_events: list[str],
    budget: Budget,
    sparse_encoder: SparseEncoder,
) -> str:
    """Write raw + canonical + chunks for one resolved entry.

    Returns:
        Either ``"processed"`` or ``"skipped"``.
    """
    # Step 1: resolve canonical_id (tier-1/2/3 + alias + flip prechecks).
    name = entry.source_id  # ingest's name extraction is best-effort in v1
    sector = fan.facets.sector
    res = await resolve_entity(
        entry,
        journal=journal,
        embed_client=embed_client,
        name=name,
        sector=sector,
        founding_year=fan.facets.founding_year,
        llm_client=llm,
        haiku_model_id=config.model_facet,
    )
    span_events.extend(res.span_events)
    if res.action in ("alias_blocked", "resolver_flipped"):
        return "skipped"
    canonical_id = res.canonical_id

    # Step 2: build the merged section list. For v1 ingest only knows about THIS
    # raw section; merge with prior raw on disk happens via the read-back path
    # in reconcile / multi-source ingest. The deterministic combined_text uses
    # the single section here.
    section = Section(
        text=body,
        reliability_rank=_reliability_for(entry.source),
        source_id=entry.source_id,
        source=entry.source,
    )
    merged = combined_text([section])
    content_hash = combined_hash([section])

    skip_key = _skip_key(
        content_hash=content_hash,
        facet_sha=prompt_template_sha("facet_extract"),
        summarize_sha=prompt_template_sha("summarize"),
        haiku_model_id=config.model_facet,
        embed_model_id=config.embed_model_id,
        chunk_strategy=CHUNK_STRATEGY_VERSION,
        taxonomy_version=config.taxonomy_version,
        reliability_rank_version=config.reliability_rank_version,
    )

    # Step 3: skip-key short-circuit unless --force.
    existing = await journal.fetch_by_key(canonical_id, entry.source, entry.source_id)
    if not force and existing:
        row = existing[0]
        if row.get("merge_state") == "complete" and row.get("skip_key") == skip_key:
            return "skipped"

    # Step 4: pending row.
    await journal.upsert_pending(
        canonical_id=canonical_id, source=entry.source, source_id=entry.source_id
    )

    # Step 5: atomic raw write.
    text_id = _text_id_for(canonical_id)
    await write_raw_atomic(
        post_mortems_root,
        text_id,
        entry.source,
        body,
        front_matter={
            "canonical_id": canonical_id,
            "source": entry.source,
            "source_id": entry.source_id,
            "content_hash": content_hash,
            "facet_prompt_hash": prompt_template_sha("facet_extract"),
            "embed_model_id": config.embed_model_id,
            "chunk_strategy_version": CHUNK_STRATEGY_VERSION,
            "taxonomy_version": config.taxonomy_version,
        },
    )

    # Step 6: atomic canonical write (one-section merge in this ingest).
    await write_canonical_atomic(
        post_mortems_root,
        text_id,
        merged,
        front_matter={
            "canonical_id": canonical_id,
            "combined_hash": content_hash,
            "skip_key": skip_key,
            "merged_at": utcnow_iso(),
            "source_ids": [f"{entry.source}:{entry.source_id}"],
        },
    )

    # Step 7: chunks + embed + qdrant. Re-upsert path: delete then re-upsert
    # all chunk points for this canonical_id so prior-run orphans don't pile up.
    if existing:
        with contextlib.suppress(Exception):
            await corpus.delete_chunks_for_canonical(canonical_id)
    payload = _build_payload(
        facets=fan.facets,
        summary=fan.summary,
        body=merged,
        slop_score=slop_score,
        sources_seen=[f"{entry.source}:{entry.source_id}"],
        text_id=text_id,
        name=name,
        provenance=entry.source,
    )
    _ = await _embed_and_upsert(
        canonical_id=canonical_id,
        body=merged,
        payload=payload,
        corpus=corpus,
        embed_client=embed_client,
        embed_model_id=config.embed_model_id,
        budget=budget,
        sparse_encoder=sparse_encoder,
    )

    # Step 8: mark complete with skip_key LAST.
    await journal.mark_complete(
        canonical_id=canonical_id,
        source=entry.source,
        source_id=entry.source_id,
        skip_key=skip_key,
        merged_at=utcnow_iso(),
        content_hash=content_hash,
    )
    return "processed"


# ─── Public entry point ────────────────────────────────────────────────────────


async def ingest(  # noqa: PLR0913, C901, PLR0912, PLR0915 — orchestration takes every dependency.
    *,
    sources: Sequence[Source],
    enrichers: Sequence[Enricher],
    journal: MergeJournal,
    corpus: Corpus,
    llm: LLMClient,
    embed_client: EmbeddingClient,
    budget: Budget,
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

    Args:
        sources: Concrete :class:`Source` adapters to fetch from. Per-source
            failures are logged and the run continues (spec line 606).
        enrichers: Optional pre-classifier enrichers (e.g. wayback fallback).
        journal: SQLite merge journal — pending/complete writers + quarantine.
        corpus: :class:`Corpus` write surface. Production is :class:`QdrantCorpus`.
        llm: LLM client for facet_extract + summarize_for_rerank.
        embed_client: Dense embedding client. Vector dim is read at the
            embedding client's level; ingest never hardcodes dim numbers.
        budget: Shared :class:`~slopmortem.budget.Budget`. The LLM and embedding
            clients reserve and settle internally.
        slop_classifier: Score-only classifier (Binoculars in production).
        config: Loaded :class:`Config`. Pulls ingest_concurrency, slop_threshold,
            model ids, taxonomy/reliability versions.
        post_mortems_root: Root for ``raw/``, ``canonical/``, ``quarantine/``.
        dry_run: When True, count entries that would be ingested but write
            nothing. No journal rows, no disk, no qdrant.
        force: When True, bypass the skip_key short-circuit and re-process
            every entry.
        sparse_encoder: Override the BM25 sparse encoder. ``None`` lazy-loads
            the production fastembed model on first call. Tests pass a
            no-op stub so they don't trigger the ~150 MB ONNX download.
        limit: Optional cap on entries gathered from sources. ``None`` runs
            unbounded; when set, sources past the cap aren't started.

    Returns:
        Counters and span event names for the run.
    """
    result = IngestResult(dry_run=dry_run)
    progress = progress or NullProgress()

    # Default sparse encoder: BM25 via fastembed. Tests stub this with a dict-returning
    # lambda so the ONNX model never loads under pytest.
    if sparse_encoder is None:
        from slopmortem.corpus.embed_sparse import encode as _encode_sparse  # noqa: PLC0415

        sparse_encoder = _encode_sparse

    # ─── Step 1: pull every entry; per-source errors counted, run continues. ───
    # `limit` short-circuits gathering — sources past the cap aren't started.
    progress.start_phase(IngestPhase.GATHER, total=limit or 0)
    entries, source_failures = await _gather_entries(
        sources, span_events=result.span_events, limit=limit
    )
    progress.end_phase(IngestPhase.GATHER)
    progress.log(f"gathered {len(entries)} entries from {len(sources)} sources")
    result.source_failures = source_failures

    # ─── Step 2: enrich + slop classify + length-floor. ───────────────────────
    progress.start_phase(IngestPhase.CLASSIFY, total=len(entries))
    keepers: list[tuple[RawEntry, str]] = []  # (entry, body) post-extract.
    for entry in entries:
        result.seen += 1
        try:
            enriched = await _enrich_pipeline(entry, enrichers)
        except Exception as exc:  # noqa: BLE001 — per-entry isolation.
            logger.warning("ingest: enricher failed for %r: %s", entry.source_id, exc)
            progress.error(
                IngestPhase.CLASSIFY, f"enricher failed for {entry.source_id}: {exc}"
            )
            result.errors += 1
            result.span_events.append(SpanEvent.INGEST_ENTRY_FAILED.value)
            progress.advance_phase(IngestPhase.CLASSIFY)
            continue

        body = _entry_summary_text(enriched, max_tokens=config.max_doc_tokens)
        if not body:
            result.skipped += 1
            progress.advance_phase(IngestPhase.CLASSIFY)
            continue

        # Slop classify; quarantine routes do NOT reach LLM/embed/qdrant.
        # Pre-vetted sources skip the LLM judge: ``curated`` is human-reviewed
        # YAML, and ``crunchbase_csv`` rows are pre-filtered to ``status=closed``
        # — running the dead-company classifier on a Wayback'd live-era homepage
        # would systematically mis-quarantine them since the body is marketing
        # copy, not a death narrative.
        if entry.source in _PRE_VETTED_SOURCES:
            slop_score = 0.0
        else:
            try:
                slop_score = await slop_classifier.score(body)
            except Exception as exc:  # noqa: BLE001 — defensive: never abort on classifier failure.
                logger.warning("ingest: slop classifier failed: %s", exc)
                progress.error(IngestPhase.CLASSIFY, f"slop classifier failed: {exc}")
                slop_score = 0.0

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
    progress.log(f"classified: {len(keepers)} kept, {result.quarantined} quarantined, {result.skipped} skipped")

    # ─── Dry-run early exit: only count, never write. ─────────────────────────
    if dry_run:
        result.would_process = len(keepers)
        return result

    if not keepers:
        return result

    # ─── Step 3: cache-warm one serial call so fan-out runs hot. ──────────────
    progress.start_phase(IngestPhase.CACHE_WARM, total=1)
    warmed, warm_creation, warm_events = await _cache_warm(
        llm=llm,
        model=config.model_summarize,
        seed_text=keepers[0][1][:1000],
    )
    progress.advance_phase(IngestPhase.CACHE_WARM)
    progress.end_phase(IngestPhase.CACHE_WARM)
    result.cache_warmed = warmed
    result.cache_creation_tokens_warm = warm_creation
    result.span_events.extend(warm_events)

    # ─── Step 4: bounded fan-out for facets + summarize. ──────────────────────
    progress.start_phase(IngestPhase.FAN_OUT, total=len(keepers))
    fanout = await _facet_summarize_fanout(keepers, llm=llm, config=config, progress=progress)
    progress.end_phase(IngestPhase.FAN_OUT)

    # ─── Step 5: read-ratio probe on first 5 fan-out responses. ───────────────
    probe = [r for r in fanout if isinstance(r, _FanoutResult)][:_CACHE_READ_RATIO_PROBE_N]
    if probe:
        total_read = sum(r.cache_read for r in probe)
        total_creation = sum(r.cache_creation for r in probe)
        denom = total_read + total_creation
        if denom > 0:
            ratio = total_read / denom
            if ratio < _CACHE_READ_RATIO_THRESHOLD:
                result.span_events.append(SpanEvent.CACHE_READ_RATIO_LOW.value)

    # ─── Step 6: per-entry sequential write phase. ────────────────────────────
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
                budget=budget,
                sparse_encoder=sparse_encoder,
            )
        except Exception as exc:  # noqa: BLE001 — spec line 606: run continues.
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
            result.errors += 1
            result.span_events.append(SpanEvent.INGEST_ENTRY_FAILED.value)
            progress.advance_phase(IngestPhase.WRITE)
            continue
        if outcome == "processed":
            result.processed += 1
        elif outcome == "skipped":
            result.skipped += 1
        progress.advance_phase(IngestPhase.WRITE)
    progress.end_phase(IngestPhase.WRITE)

    return result
