"""Smoke tests for the shared `slopmortem.cli_progress` widget.

Three checks:

1. ``RichPhaseProgress[FakeEnum]`` advances and ends without raising under a
   forced-terminal in-memory console.
2. ``corpus_recorder._make_progress_ctx`` returns a ``_RichIngestProgress``
   when stderr *is* a TTY.
3. ``corpus_recorder._make_progress_ctx`` returns a no-op ``nullcontext`` when
   stderr is not a TTY.

The TTY-gate tests target the actual helper used by ``_record`` rather than
re-evaluating the gate expression in the test body, so a divergence in the
production code path is caught.

Constructing `Console` with ``file=StringIO(), force_terminal=True``
exercises the Rich render pipeline (otherwise it auto-detects no-color and
short-circuits) while keeping output off the test runner's stdout/stderr.
"""

from __future__ import annotations

from enum import StrEnum
from io import StringIO
from typing import TYPE_CHECKING, Self

from rich.console import Console

from slopmortem import cli_progress
from slopmortem.cli_progress import RichPhaseProgress

if TYPE_CHECKING:
    import pytest


class _FakePhase(StrEnum):
    ALPHA = "alpha"
    BETA = "beta"


def test_rich_phase_progress_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drive every public method on a forced-terminal in-memory console."""
    captured = StringIO()

    def _make_console(**_kwargs: object) -> Console:
        return Console(file=captured, force_terminal=True, width=120)

    # The widget constructs ``Console(stderr=True)`` directly; redirecting the
    # symbol pins output to ``captured`` without forcing a constructor argument
    # onto the public API.
    monkeypatch.setattr(cli_progress, "Console", _make_console)

    labels = {_FakePhase.ALPHA: "Alpha", _FakePhase.BETA: "Beta"}
    with RichPhaseProgress(labels) as progress:
        progress.start_phase(_FakePhase.ALPHA, total=3)
        progress.advance_phase(_FakePhase.ALPHA)
        progress.advance_phase(_FakePhase.ALPHA, n=2)
        progress.set_phase_status(_FakePhase.ALPHA, "running")
        progress.set_phase_status(_FakePhase.ALPHA, None)
        progress.end_phase(_FakePhase.ALPHA)

        progress.start_phase(_FakePhase.BETA, total=None)
        progress.log("status line")
        progress.error(_FakePhase.BETA, "kaboom")
        progress.end_phase(_FakePhase.BETA)

    output = captured.getvalue()
    # Labels appear in the rendered descriptions; presence is enough to confirm
    # the widget produced output rather than silently no-op'ing.
    assert "Alpha" in output
    assert "Beta" in output


def test_make_progress_ctx_returns_rich_progress_when_stderr_is_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TTY stderr → ``_make_progress_ctx`` constructs a ``_RichIngestProgress``.

    Spies the class symbol the helper looks up so the test can witness the
    construction call without entering the Live render against the runner's
    stderr.
    """
    from slopmortem.evals import corpus_recorder  # noqa: PLC0415 - test-only import

    monkeypatch.setattr(corpus_recorder.sys.stderr, "isatty", lambda: True)

    constructed: list[object] = []

    class _Spy:
        def __init__(self) -> None:
            constructed.append(self)

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(corpus_recorder, "_RichIngestProgress", _Spy)

    ctx = corpus_recorder._make_progress_ctx()
    with ctx as bar:
        assert bar is not None
        assert isinstance(bar, _Spy)
    assert len(constructed) == 1


def test_make_progress_ctx_returns_nullcontext_when_stderr_not_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-TTY stderr → ``_make_progress_ctx`` yields ``None`` (nullcontext).

    Piped invocations (CI, redirect-to-file) must skip the Live render so log
    capture stays readable. The spy ensures ``_RichIngestProgress`` is not
    instantiated on the no-TTY branch.
    """
    from slopmortem.evals import corpus_recorder  # noqa: PLC0415 - test-only import

    monkeypatch.setattr(corpus_recorder.sys.stderr, "isatty", lambda: False)

    constructed: list[object] = []

    class _Spy:
        def __init__(self) -> None:
            constructed.append(self)

    monkeypatch.setattr(corpus_recorder, "_RichIngestProgress", _Spy)

    ctx = corpus_recorder._make_progress_ctx()
    with ctx as bar:
        assert bar is None
    assert constructed == []
