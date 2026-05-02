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

    Methods are no-op-safe. :class:`NullRecordProgress` is the default so the
    recorder stays decoupled from any UI library; the runner wires a Rich
    implementation. Mirrors :class:`slopmortem.pipeline.QueryProgress`.
    """

    def start_phase(self, phase: RecordPhase, total: int | None) -> None:
        """Announce *phase* with an expected ``total`` of advances."""

    def advance_phase(self, phase: RecordPhase, n: int = 1) -> None:
        """Advance *phase*'s bar by ``n``."""

    def end_phase(self, phase: RecordPhase) -> None:
        """Mark *phase* complete."""

    def set_phase_status(self, phase: RecordPhase, status: str | None) -> None:
        """Set or clear a transient status suffix on *phase*'s display label."""

    def log(self, message: str) -> None:
        """Emit a one-off status line."""

    def error(self, phase: RecordPhase, message: str) -> None:
        """Record an error against *phase*."""


class NullRecordProgress:
    """No-op :class:`RecordProgress` used when no display surface is attached."""

    def start_phase(self, phase: RecordPhase, total: int | None) -> None:
        """No-op."""

    def advance_phase(self, phase: RecordPhase, n: int = 1) -> None:
        """No-op."""

    def end_phase(self, phase: RecordPhase) -> None:
        """No-op."""

    def set_phase_status(self, phase: RecordPhase, status: str | None) -> None:
        """No-op."""

    def log(self, message: str) -> None:
        """No-op."""

    def error(self, phase: RecordPhase, message: str) -> None:
        """No-op."""
