"""Tests for record_cassettes_for_inputs: atomic swap, tmp cleanup, Tavily forced off."""

from __future__ import annotations

import inspect
import os
from typing import TYPE_CHECKING

import pytest

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


def test_query_progress_bridge_collapses_to_rows_status() -> None:
    """The bridge folds every inner phase event into ``ROWS``'s status suffix."""

    class _FakeSink:
        def __init__(self) -> None:
            self.statuses: list[str | None] = []
            self.errors: list[tuple[RecordPhase, str]] = []
            self.logs: list[str] = []

        def start_phase(self, phase: RecordPhase, total: int | None) -> None:
            del phase, total
            pytest.fail(_NO_INNER_TASKS_MSG)

        def advance_phase(self, phase: RecordPhase, n: int = 1) -> None:
            del phase, n
            pytest.fail(_NO_INNER_TASKS_MSG)

        def end_phase(self, phase: RecordPhase) -> None:
            del phase
            pytest.fail(_NO_INNER_TASKS_MSG)

        def set_phase_status(self, phase: RecordPhase, status: str | None) -> None:
            assert phase == RecordPhase.ROWS
            self.statuses.append(status)

        def log(self, message: str) -> None:
            self.logs.append(message)

        def error(self, phase: RecordPhase, message: str) -> None:
            self.errors.append((phase, message))

        def cost_update(self, spent_usd: float, max_usd: float) -> None:
            del spent_usd, max_usd

    sink = _FakeSink()
    bridge = _QueryProgressBridge(sink)

    bridge.start_phase(QueryPhase.FACET_EXTRACT, total=1)
    assert sink.statuses[-1] == "extracting facets"

    bridge.start_phase(QueryPhase.SYNTHESIZE, total=2)
    assert sink.statuses[-1] == "synthesizing post-mortems 0/2"
    bridge.set_phase_status(QueryPhase.SYNTHESIZE, "warming prompt cache")
    assert sink.statuses[-1] == "synthesizing post-mortems 0/2 — warming prompt cache"
    bridge.advance_phase(QueryPhase.SYNTHESIZE)
    bridge.set_phase_status(QueryPhase.SYNTHESIZE, None)
    assert sink.statuses[-1] == "synthesizing post-mortems 1/2"

    bridge.error(QueryPhase.SYNTHESIZE, "boom")
    assert sink.errors == [(RecordPhase.ROWS, "boom")]

    bridge.log("hi")
    assert sink.logs == ["hi"]

    # end_phase must not flash an empty status between sub-steps.
    statuses_before = list(sink.statuses)
    bridge.end_phase(QueryPhase.SYNTHESIZE)
    assert sink.statuses == statuses_before
