"""Tests for record_cassettes_for_inputs: atomic swap, tmp cleanup, Tavily forced off."""

from __future__ import annotations

import inspect
import os
from typing import TYPE_CHECKING

from slopmortem.evals.recording_helper import (
    _AggregateProgressBridge,
    _atomic_swap,
    _sweep_stale_recording_dirs,
    record_cassettes_for_inputs,
)
from slopmortem.evals.recording_progress import (
    NullRecordProgress,
    RecordPhase,
    RecordProgress,
)
from slopmortem.pipeline import QueryPhase

if TYPE_CHECKING:
    from pathlib import Path


def test_sweep_removes_only_stale_recording_dirs(tmp_path: Path) -> None:
    fresh = tmp_path / "scope.42.abc.recording"
    fresh.mkdir()
    stale = tmp_path / "scope.99.def.recording"
    stale.mkdir()
    # Backdate by 25h so the sweeper sees it as stale.
    old_mtime = fresh.stat().st_mtime - 25 * 3600
    os.utime(stale, (old_mtime, old_mtime))
    # Sibling that isn't a tmp dir; the sweeper must leave it alone.
    keep = tmp_path / "scope"
    keep.mkdir()

    _sweep_stale_recording_dirs(tmp_path, max_age_seconds=24 * 3600)

    assert fresh.exists()
    assert not stale.exists()
    assert keep.exists()


def test_atomic_swap_uses_two_step_rename(tmp_path: Path) -> None:
    real = tmp_path / "scope"
    real.mkdir()
    (real / "old.json").write_text("old")
    new_tmp = tmp_path / "scope.42.abc.recording"
    new_tmp.mkdir()
    (new_tmp / "new.json").write_text("new")

    _atomic_swap(tmp_dir=new_tmp, real_dir=real)

    assert (real / "new.json").exists()
    assert not (real / "old.json").exists()
    assert not new_tmp.exists()
    assert not (real.parent / (real.name + ".old")).exists()


def test_atomic_swap_handles_missing_real_dir(tmp_path: Path) -> None:
    real = tmp_path / "scope"
    new_tmp = tmp_path / "scope.42.abc.recording"
    new_tmp.mkdir()
    (new_tmp / "new.json").write_text("new")

    _atomic_swap(tmp_dir=new_tmp, real_dir=real)

    assert (real / "new.json").exists()


def test_record_cassettes_for_inputs_accepts_progress_kwarg() -> None:
    """``progress`` is a keyword arg with a default, so existing callers stay valid.

    Recording helper does live-API work; full end-to-end coverage is gated on
    ``RUN_LIVE`` cassettes. This thin signature check verifies the new param
    is plumbed through with a backward-compatible default and that
    :class:`NullRecordProgress` satisfies the protocol — the path the runner
    takes when ``sys.stderr`` isn't a TTY.
    """
    sig = inspect.signature(record_cassettes_for_inputs)
    assert "progress" in sig.parameters
    progress_param = sig.parameters["progress"]
    assert progress_param.default is None
    assert isinstance(NullRecordProgress(), RecordProgress)


class _FakeSink:
    """Minimal RecordProgress capturing advances/logs/errors for assertions."""

    def __init__(self) -> None:
        self.advances: list[tuple[RecordPhase, int]] = []
        self.errors: list[tuple[RecordPhase, str]] = []
        self.logs: list[str] = []

    def start_phase(self, phase: RecordPhase, total: int | None) -> None:
        del phase, total

    def advance_phase(self, phase: RecordPhase, n: int = 1) -> None:
        self.advances.append((phase, n))

    def end_phase(self, phase: RecordPhase) -> None:
        del phase

    def set_phase_status(self, phase: RecordPhase, status: str | None) -> None:
        del phase, status

    def log(self, message: str) -> None:
        self.logs.append(message)

    def error(self, phase: RecordPhase, message: str) -> None:
        self.errors.append((phase, message))

    def cost_update(self, spent_usd: float, max_usd: float) -> None:
        del spent_usd, max_usd


def test_aggregate_bridge_funnels_inner_advances_to_rows() -> None:
    """Every inner phase advance becomes one tick on the shared ROWS bar."""
    sink = _FakeSink()
    bridge = _AggregateProgressBridge(sink, ticks_per_row=8)  # 3 fixed + 5 synth

    bridge.start_phase(QueryPhase.FACET_EXTRACT, total=1)
    bridge.advance_phase(QueryPhase.FACET_EXTRACT)
    bridge.advance_phase(QueryPhase.RETRIEVE)
    bridge.advance_phase(QueryPhase.RERANK)
    for _ in range(5):
        bridge.advance_phase(QueryPhase.SYNTHESIZE)

    # Each call lands on RecordPhase.ROWS with delta=1.
    assert sink.advances == [(RecordPhase.ROWS, 1)] * 8


def test_aggregate_bridge_top_up_fills_unfired_ticks() -> None:
    """Rows that fire fewer SYNTHESIZE advances than budgeted top up at end."""
    sink = _FakeSink()
    bridge = _AggregateProgressBridge(sink, ticks_per_row=8)

    bridge.advance_phase(QueryPhase.FACET_EXTRACT)
    bridge.advance_phase(QueryPhase.RETRIEVE)
    bridge.advance_phase(QueryPhase.RERANK)
    # Only 2 of 5 syntheses fired (e.g., retrieve returned <N candidates).
    bridge.advance_phase(QueryPhase.SYNTHESIZE)
    bridge.advance_phase(QueryPhase.SYNTHESIZE)
    bridge.top_up()

    total_ticked = sum(n for _, n in sink.advances)
    assert total_ticked == 8

    # Calling top_up twice is idempotent — never overshoot.
    bridge.top_up()
    assert sum(n for _, n in sink.advances) == 8


def test_aggregate_bridge_clamps_overshoot() -> None:
    """A misbehaving inner stage can't push the bar past its declared total."""
    sink = _FakeSink()
    bridge = _AggregateProgressBridge(sink, ticks_per_row=4)

    for _ in range(10):
        bridge.advance_phase(QueryPhase.SYNTHESIZE)

    assert sum(n for _, n in sink.advances) == 4


def test_aggregate_bridge_forwards_log_and_error() -> None:
    """Logs pass through; errors attach to ROWS regardless of source phase."""
    sink = _FakeSink()
    bridge = _AggregateProgressBridge(sink, ticks_per_row=8)

    bridge.log("hi")
    bridge.error(QueryPhase.SYNTHESIZE, "boom")

    assert sink.logs == ["hi"]
    assert sink.errors == [(RecordPhase.ROWS, "boom")]
