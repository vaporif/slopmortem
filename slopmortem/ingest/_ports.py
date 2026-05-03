"""Port surface for the ingest package: protocols, type aliases, dataclasses, enums.

Leaf within the ingest package — imports nothing from sibling ingest modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "INGEST_PHASE_LABELS",
    "Corpus",
    "IngestPhase",
    "IngestProgress",
    "IngestResult",
    "NullProgress",
    "SlopClassifier",
    "SparseEncoder",
    "_Point",
]

type SparseEncoder = Callable[[str], dict[int, float]]

# Cap on indexed per-entry exception attributes so a pathological run can't
# blow past Laminar's per-span attribute limit. Beyond this we record only
# ``errors.truncated_count``.
_MAX_RECORDED_ERRORS: Final[int] = 50


@runtime_checkable
class Corpus(Protocol):
    """Narrow corpus surface ingest depends on; prod impl is :class:`QdrantCorpus`."""

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

    Default :class:`NullProgress` keeps the orchestrator decoupled from any
    UI library; the CLI wires a Rich impl.
    """

    def start_phase(self, phase: IngestPhase, total: int | None) -> None:
        """``total=None`` marks the phase indeterminate (Rich pulses; ETA blank)."""

    def advance_phase(self, phase: IngestPhase, n: int = 1) -> None: ...

    def end_phase(self, phase: IngestPhase) -> None: ...

    def log(self, message: str) -> None: ...

    def error(self, phase: IngestPhase, message: str) -> None: ...


class NullProgress:
    """No-op :class:`IngestProgress` for when no display surface is attached."""

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
