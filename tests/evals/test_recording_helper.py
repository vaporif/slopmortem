"""Tests for record_cassettes_for_inputs(): atomic swap, tmp cleanup, Tavily forced off."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from slopmortem.evals.recording_helper import (
    _atomic_swap,
    _sweep_stale_recording_dirs,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_sweep_removes_only_stale_recording_dirs(tmp_path: Path) -> None:
    fresh = tmp_path / "scope.42.abc.recording"
    fresh.mkdir()
    stale = tmp_path / "scope.99.def.recording"
    stale.mkdir()
    # Touch back 25h.
    old_mtime = fresh.stat().st_mtime - 25 * 3600
    os.utime(stale, (old_mtime, old_mtime))
    # Sibling that's not a tmp dir; never touched.
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
