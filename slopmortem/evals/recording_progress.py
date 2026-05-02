"""Progress Protocol for the eval cassette recorder.

One phase task: ``ROWS`` (per-input outer loop). Inner ``run_query`` phases —
facet/embed/rerank/synthesize — collapse onto it as transient status suffix
ticks via :class:`_AggregateProgressBridge` in ``recording_helper``. Live
spend is surfaced through per-row log lines and the post-run footer panel,
not a dedicated bar.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable


class RecordPhase(StrEnum):
    """Phase keys driven by :func:`record_cassettes_for_inputs`."""

    ROWS = "rows"


@runtime_checkable
class RecordProgress(Protocol):
    """Phase-level progress hooks for the eval cassette recorder.

    Default :class:`NullRecordProgress` keeps the recorder decoupled from any
    UI library; the runner wires a Rich implementation.
    """

    def start_phase(self, phase: RecordPhase, total: int | None) -> None: ...

    def advance_phase(self, phase: RecordPhase, n: int = 1) -> None: ...

    def end_phase(self, phase: RecordPhase) -> None: ...

    def set_phase_status(self, phase: RecordPhase, status: str | None) -> None: ...

    def log(self, message: str) -> None: ...

    def error(self, phase: RecordPhase, message: str) -> None: ...


class NullRecordProgress:
    """No-op :class:`RecordProgress` used when no display surface is attached."""

    def start_phase(self, phase: RecordPhase, total: int | None) -> None: ...

    def advance_phase(self, phase: RecordPhase, n: int = 1) -> None: ...

    def end_phase(self, phase: RecordPhase) -> None: ...

    def set_phase_status(self, phase: RecordPhase, status: str | None) -> None: ...

    def log(self, message: str) -> None: ...

    def error(self, phase: RecordPhase, message: str) -> None: ...
