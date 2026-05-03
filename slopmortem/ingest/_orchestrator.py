# pyright: reportAny=false
"""Shared types, protocols, dataclasses, and pure helpers for the ingest package.

Leaf of the package's import graph: imports nothing from sibling ingest modules.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Protocol, cast, runtime_checkable

from slopmortem.corpus import (
    extract_clean,
)
from slopmortem.llm import prompt_template_sha, render_prompt
from slopmortem.models import (
    CandidatePayload,
    Facets,
)
from slopmortem.tracing import SpanEvent

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from slopmortem.corpus.sources import Enricher, Source
    from slopmortem.llm import LLMClient
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
]

type SparseEncoder = Callable[[str], dict[int, float]]

logger = logging.getLogger(__name__)

# Cap on indexed per-entry exception attributes so a pathological run can't
# blow past Laminar's per-span attribute limit. Beyond this we record only
# ``errors.truncated_count``.
_MAX_RECORDED_ERRORS: Final[int] = 50

# merge_text orders sections by this. Curated > HN > Wayback > everything else.
_RELIABILITY_RANK: Final[dict[str, int]] = {
    "curated": 0,
    "hn": 1,
    "wayback": 2,
    "crunchbase": 3,
}


@runtime_checkable
class Corpus(Protocol):
    """Narrow corpus surface ingest depends on; prod impl is `QdrantCorpus`."""

    async def upsert_chunk(self, point: object) -> None: ...

    async def has_chunks(self, canonical_id: str) -> bool: ...

    async def delete_chunks_for_canonical(self, canonical_id: str) -> None: ...


class IngestPhase(StrEnum):
    GATHER = "gather"
    CLASSIFY = "classify"
    CACHE_WARM = "cache_warm"
    FAN_OUT = "fan_out"
    WRITE = "write"


# Keyed on IngestPhase so adding a phase fails type-check at every consumer
# until it gets a label here.
INGEST_PHASE_LABELS: dict[IngestPhase, str] = {
    IngestPhase.GATHER: "Gathering entries from sources",
    IngestPhase.CLASSIFY: "Classifying / slop-filtering",
    IngestPhase.CACHE_WARM: "Warming prompt cache",
    IngestPhase.FAN_OUT: "Facets + summarize fan-out",
    IngestPhase.WRITE: "Entity-resolve / chunk / qdrant",
}


@runtime_checkable
class IngestProgress(Protocol):
    """Phase-level progress hooks.

    Default `NullProgress` keeps the orchestrator decoupled from any
    UI library; the CLI wires a Rich impl.
    """

    def start_phase(self, phase: IngestPhase, total: int | None) -> None:
        """``total=None`` marks the phase indeterminate (Rich pulses; ETA blank)."""

    def advance_phase(self, phase: IngestPhase, n: int = 1) -> None: ...

    def end_phase(self, phase: IngestPhase) -> None: ...

    def log(self, message: str) -> None: ...

    def error(self, phase: IngestPhase, message: str) -> None: ...


class NullProgress:
    """No-op `IngestProgress` for when no display surface is attached."""

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
    """Stand-in for a Qdrant point; prod uses ``qdrant_client.models.PointStruct``."""

    id: str
    vector: dict[str, object]
    payload: dict[str, object]


@dataclass
class InMemoryCorpus:
    """In-memory `Corpus` for tests; not used in production."""

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
    """Deterministic test `SlopClassifier`; ``scores`` overrides by text-key prefix."""

    default_score: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)

    async def score(self, text: str) -> float:
        for key, val in self.scores.items():
            if text.startswith(key) or key in text:
                return val
        return self.default_score


@dataclass
class HaikuSlopClassifier:
    """LLM-backed slop classifier.

    Asks Haiku whether a text describes a dead company; returns 0.0 if yes,
    else 1.0 (above the default ``slop_threshold=0.7``, so quarantines).

    ``char_limit=6000`` so the demise narrative falls inside the window for long
    obituaries (Sun, WeWork). Tighter 1500-char caps caused false-negative
    quarantines.
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
    """Truncate *text* to *max_tokens* via cl100k_base.

    Anthropic's tokenizer isn't published; cl100k_base agrees within ~10%
    on English prose, well inside the truncation budget's headroom.
    """
    if max_tokens <= 0:
        return text
    import tiktoken  # noqa: PLC0415 - heavy dep; lazy

    enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return enc.decode(tokens[:max_tokens])


def _entry_summary_text(entry: RawEntry, *, max_tokens: int) -> str:  # pyright: ignore[reportUnusedFunction]  -- imported by _ingest.py
    """Return entry body text, clipped to *max_tokens*.

    Clipped to bound LLM input cost on long-tail articles (Wikipedia entries
    can run 60KB+ after trafilatura).
    """
    if entry.markdown_text:
        return _truncate_to_tokens(entry.markdown_text, max_tokens)
    if entry.raw_html:
        return _truncate_to_tokens(extract_clean(entry.raw_html), max_tokens)
    return ""


async def _enrich_pipeline(entry: RawEntry, enrichers: Sequence[Enricher]) -> RawEntry:  # pyright: ignore[reportUnusedFunction]  -- imported by _ingest.py
    cur = entry
    for e in enrichers:
        cur = await e.enrich(cur)
    return cur


async def _gather_entries(  # pyright: ignore[reportUnusedFunction]  -- imported by _ingest.py
    sources: Sequence[Source],
    *,
    span_events: list[str],
    limit: int | None = None,
    progress: IngestProgress | None = None,
) -> tuple[list[RawEntry], int]:
    """Per-source failures are logged and counted, never abort the run.

    ``--limit`` is a real fast-path knob, not a post-gather slice: sources
    beyond the cap aren't started, and in-progress sources break out of their
    async iterator on the next yield.
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
