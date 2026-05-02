"""Tests for the recorder's Rich progress widget and the no-op fallback.

Three checks:

1. ``RichRecordProgress`` smoke: every public method runs against a forced-
   terminal in-memory console without raising.
2. Cost-bar correctness: ``cost_update(0.50, 2.00)`` lazily creates the
   ``COST`` task and lands it with ``total ≈ 2.00`` and ``completed ≈ 0.50``.
3. ``NullRecordProgress`` is a no-op: every method returns ``None`` and
   triggers no observable side effect.
"""

from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING

from rich.console import Console

from slopmortem import cli_progress
from slopmortem.evals.recording_progress import NullRecordProgress, RecordPhase
from slopmortem.evals.render import RichRecordProgress

if TYPE_CHECKING:
    import pytest


def test_rich_record_progress_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive every phase + cost_update against a forced-terminal in-memory console."""
    captured = StringIO()

    def _make_console(**_kwargs: object) -> Console:
        return Console(file=captured, force_terminal=True, width=120)

    # Pin the Rich console used by the inherited widget so output stays off
    # the test runner's stderr.
    monkeypatch.setattr(cli_progress, "Console", _make_console)

    with RichRecordProgress() as progress:
        progress.start_phase(RecordPhase.ROWS, total=3)
        progress.advance_phase(RecordPhase.ROWS)
        progress.cost_update(0.10, 2.00)
        progress.start_phase(RecordPhase.FACET_EXTRACT, total=1)
        progress.end_phase(RecordPhase.FACET_EXTRACT)
        progress.start_phase(RecordPhase.EMBED, total=5)
        progress.advance_phase(RecordPhase.EMBED, n=5)
        progress.end_phase(RecordPhase.EMBED)
        progress.set_phase_status(RecordPhase.SYNTHESIZE, "warming")
        progress.error(RecordPhase.SYNTHESIZE, "kaboom")
        progress.cost_update(0.50, 2.00)
        progress.end_phase(RecordPhase.ROWS)

    output = captured.getvalue()
    # Labels appear in the rendered descriptions; presence is enough to confirm
    # the widget produced output rather than silently no-op'ing.
    assert "Rows" in output
    assert "Spend" in output


def test_rich_record_progress_cost_bar_lazy_create(monkeypatch: pytest.MonkeyPatch) -> None:
    """``cost_update`` lazily creates the COST task with the right total/completed."""
    captured = StringIO()

    def _make_console(**_kwargs: object) -> Console:
        return Console(file=captured, force_terminal=True, width=120)

    monkeypatch.setattr(cli_progress, "Console", _make_console)

    with RichRecordProgress() as progress:
        # No prior start_phase(COST, ...) — cost_update must lazily create it.
        progress.cost_update(0.50, 2.00)
        # Reach into the parent's task table to assert the bar landed where the
        # spec says it should. Subclass access of the inherited ``_tasks`` /
        # ``_progress`` is the same scope as the cost_update implementation.
        task_id = progress._tasks[RecordPhase.COST]
        task = progress._progress.tasks[task_id]
        assert task.completed == 0.50
        # ``cost_update`` clamps the total to ``max(max_usd, 1.001)`` so the
        # bar still renders as a fill at low caps. Above the clamp threshold
        # the configured cap shows through verbatim.
        assert task.total == 2.00

        # Subsequent calls update the same task in place; no second task added.
        progress.cost_update(1.25, 2.00)
        assert progress._progress.tasks[task_id].completed == 1.25
        assert progress._progress.tasks[task_id].total == 2.00


def test_null_record_progress_is_pure_noop() -> None:
    """Every method returns ``None`` and produces no observable state change."""
    null = NullRecordProgress()
    assert null.start_phase(RecordPhase.ROWS, total=3) is None
    assert null.advance_phase(RecordPhase.ROWS) is None
    assert null.advance_phase(RecordPhase.ROWS, n=2) is None
    assert null.end_phase(RecordPhase.ROWS) is None
    assert null.set_phase_status(RecordPhase.FACET_EXTRACT, "x") is None
    assert null.set_phase_status(RecordPhase.FACET_EXTRACT, None) is None
    assert null.log("hello") is None
    assert null.error(RecordPhase.RERANK, "bad") is None
    assert null.cost_update(0.5, 2.0) is None
