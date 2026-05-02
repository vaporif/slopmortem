"""Tests for record_cassettes_for_inputs: atomic swap, tmp cleanup, Tavily forced off."""

from __future__ import annotations

import inspect
import os
from typing import TYPE_CHECKING

from slopmortem.evals.recording_helper import (
    _atomic_swap,
    _QueryProgressBridge,
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


def test_query_progress_bridge_translates_phases() -> None:
    """The bridge maps every ``QueryPhase`` to the matching ``RecordPhase``.

    Drives the four pipeline events plus ``log`` / ``error`` / status
    against a fake sink that records every call. Verifies the retrieve →
    embed mapping explicitly so the spec's "drop or remap" trade-off is
    pinned in code.
    """

    class _FakeSink:
        def __init__(self) -> None:
            self.calls: list[tuple[object, ...]] = []

        def start_phase(self, phase: RecordPhase, total: int | None) -> None:
            self.calls.append(("start", phase, total))

        def advance_phase(self, phase: RecordPhase, n: int = 1) -> None:
            self.calls.append(("advance", phase, n))

        def end_phase(self, phase: RecordPhase) -> None:
            self.calls.append(("end", phase))

        def set_phase_status(self, phase: RecordPhase, status: str | None) -> None:
            self.calls.append(("status", phase, status))

        def log(self, message: str) -> None:
            self.calls.append(("log", message))

        def error(self, phase: RecordPhase, message: str) -> None:
            self.calls.append(("error", phase, message))

        def cost_update(self, spent_usd: float, max_usd: float) -> None:
            self.calls.append(("cost", spent_usd, max_usd))

    sink = _FakeSink()
    bridge = _QueryProgressBridge(sink)

    bridge.start_phase(QueryPhase.FACET_EXTRACT, total=1)
    bridge.advance_phase(QueryPhase.FACET_EXTRACT)
    bridge.end_phase(QueryPhase.FACET_EXTRACT)
    bridge.start_phase(QueryPhase.RETRIEVE, total=2)
    bridge.advance_phase(QueryPhase.RETRIEVE, n=2)
    bridge.end_phase(QueryPhase.RETRIEVE)
    bridge.start_phase(QueryPhase.RERANK, total=1)
    bridge.set_phase_status(QueryPhase.RERANK, "running")
    bridge.end_phase(QueryPhase.RERANK)
    bridge.start_phase(QueryPhase.SYNTHESIZE, total=4)
    bridge.error(QueryPhase.SYNTHESIZE, "boom")
    bridge.advance_phase(QueryPhase.SYNTHESIZE, n=4)
    bridge.end_phase(QueryPhase.SYNTHESIZE)
    bridge.log("hi")

    expected = [
        ("start", RecordPhase.FACET_EXTRACT, 1),
        ("advance", RecordPhase.FACET_EXTRACT, 1),
        ("end", RecordPhase.FACET_EXTRACT),
        # ``QueryPhase.RETRIEVE`` is the only mapping the spec leaves a choice
        # on; pinning it to ``RecordPhase.EMBED`` here matches the bridge's
        # ``_QUERY_TO_RECORD_PHASE`` table and the rationale in the comment.
        ("start", RecordPhase.EMBED, 2),
        ("advance", RecordPhase.EMBED, 2),
        ("end", RecordPhase.EMBED),
        ("start", RecordPhase.RERANK, 1),
        ("status", RecordPhase.RERANK, "running"),
        ("end", RecordPhase.RERANK),
        ("start", RecordPhase.SYNTHESIZE, 4),
        ("error", RecordPhase.SYNTHESIZE, "boom"),
        ("advance", RecordPhase.SYNTHESIZE, 4),
        ("end", RecordPhase.SYNTHESIZE),
        ("log", "hi"),
    ]
    assert sink.calls == expected
