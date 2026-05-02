"""Progress Protocol for the eval cassette recorder.

Mirrors :class:`slopmortem.pipeline.QueryProgress`'s surface so the
recorder's per-row inner phases (facet/rerank/synthesize) can flow into the
same display the helper drives. Adds a single ``cost_update`` hook to surface
the live spend ceiling — recording is the only path in the project that burns
real OpenRouter money under a hard cap, so the bar mutates as spend accrues
rather than waiting until the post-run footer.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable


class RecordPhase(StrEnum):
    """Phase keys driven by :func:`record_cassettes_for_inputs`.

    The first five mirror what the recorder actually drives during a run:
    ``ROWS`` is the per-input outer loop; ``FACET_EXTRACT`` / ``RERANK`` /
    ``SYNTHESIZE`` come from the inner ``run_query`` via the bridge; ``EMBED``
    is driven from the bridge's ``QueryPhase.RETRIEVE`` mapping (retrieve is
    the only place the recorder touches the embedding wrapper).

    ``COST`` is synthetic — never emitted as a pipeline event. It exists to
    give :class:`RichRecordProgress` a phase task to mutate via
    ``cost_update`` so the spend ceiling reads as a filling bar inside the
    same Progress widget. A small abuse of the phase abstraction, accepted
    to keep the entire surface inside one render frame.
    """

    ROWS = "rows"
    FACET_EXTRACT = "facet_extract"
    RERANK = "rerank"
    SYNTHESIZE = "synthesize"
    EMBED = "embed"
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
