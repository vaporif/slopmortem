"""Progress Protocol for the eval cassette recorder.

The recorder runs many rows under a hard $ cap, so the display only carries
two phase tasks: ``ROWS`` (per-input outer loop) and ``COST`` (synthetic
spend meter mutated via ``cost_update``). Inner ``run_query`` phases —
facet/embed/rerank/synthesize — collapse onto the ``ROWS`` task as a
transient status suffix via :class:`_QueryProgressBridge` in
``recording_helper``; that keeps the screen stable across rows instead of
showing four sub-bars that reset on every iteration.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable


class RecordPhase(StrEnum):
    """Phase keys driven by :func:`record_cassettes_for_inputs`.

    ``ROWS`` is the per-input outer loop; the bridge appends a transient
    status suffix (e.g. ``synthesizing post-mortems 1/2 — warming prompt
    cache``) so the current sub-step is visible without dedicated tasks.

    ``COST`` is synthetic — never emitted as a pipeline event. It exists to
    give :class:`RichRecordProgress` a phase task to mutate via
    ``cost_update`` so the spend ceiling reads as a filling bar inside the
    same Progress widget.
    """

    ROWS = "rows"
    COST = "cost"


@runtime_checkable
class RecordProgress(Protocol):
    """Phase-level progress hooks for the eval cassette recorder.

    Methods are no-op-safe. :class:`NullRecordProgress` is the default so the
    recorder stays decoupled from any UI library; the runner wires a Rich
    implementation. Mirrors :class:`slopmortem.pipeline.QueryProgress` plus
    one extra ``cost_update`` for the spend meter.
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

    def cost_update(self, spent_usd: float, max_usd: float) -> None:
        """Surface running USD spend against the configured ceiling."""


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

    def cost_update(self, spent_usd: float, max_usd: float) -> None:
        """No-op."""
