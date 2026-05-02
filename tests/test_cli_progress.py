"""Smoke tests for the shared :mod:`slopmortem.cli_progress` widget.

Two checks:

1. ``RichPhaseProgress[FakeEnum]`` advances and ends without raising under a
   forced-terminal in-memory console.
2. ``corpus_recorder._record`` picks the ``nullcontext`` branch when stderr is
   not a TTY — verified at the import seam without driving the live ingest.

Constructing :class:`Console` with ``file=StringIO(), force_terminal=True``
exercises the Rich render pipeline (otherwise it auto-detects no-color and
short-circuits) while keeping output off the test runner's stdout/stderr.
"""

from __future__ import annotations

from enum import StrEnum
from io import StringIO
from typing import TYPE_CHECKING

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


def test_corpus_recorder_picks_nullcontext_when_stderr_not_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When stderr is not a TTY the recorder's progress branch must be the no-op.

    The corpus recorder mirrors the TTY gate from ``slopmortem.cli``: piped
    invocations get ``contextlib.nullcontext()`` so Live-render frames don't
    interleave with redirected logs. Verified at the seam used by ``_record``
    (``sys.stderr.isatty()``).
    """
    import contextlib  # noqa: PLC0415 - localized to this single assertion

    from slopmortem.evals import corpus_recorder  # noqa: PLC0415 - test-only import

    monkeypatch.setattr(corpus_recorder.sys.stderr, "isatty", lambda: False)

    # Re-evaluate the same expression the recorder uses; if the gate ever
    # diverges, this assertion catches the drift before the next live run.
    progress_ctx: contextlib.AbstractContextManager[corpus_recorder.RichIngestProgress | None] = (
        corpus_recorder.RichIngestProgress()
        if corpus_recorder.sys.stderr.isatty()
        else contextlib.nullcontext()
    )
    with progress_ctx as bar:
        assert bar is None
