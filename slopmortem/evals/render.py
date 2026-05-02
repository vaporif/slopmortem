"""Rich UI for the eval cassette recorder: phase progress + post-run footer panel.

:class:`RichRecordProgress` wraps the shared :class:`RichPhaseProgress` widget
with the recorder's phase labels and adds the ``cost_update`` hook that drives
the synthetic ``COST`` bar. :func:`render_record_footer` mirrors the shape of
``slopmortem.cli._render_query_footer`` — same border style, same `` • ``-joined
facts — so the recorder's done state reads consistently with the query CLI.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.panel import Panel

from slopmortem.cli_progress import RichPhaseProgress
from slopmortem.evals.recording_progress import RecordPhase

if TYPE_CHECKING:
    from rich.console import Console


_RECORD_PHASE_LABELS: dict[RecordPhase, str] = {
    RecordPhase.ROWS: "Rows",
    RecordPhase.COST: "Spend",
}


class RichRecordProgress(RichPhaseProgress[RecordPhase]):
    """Rich-backed :class:`RecordProgress` impl shared by ``runner._run_record``.

    Inherits the generic phase widget; adds a single ``cost_update`` method
    that lazily creates the synthetic ``COST`` task on first call and then
    mutates its ``total`` / ``completed`` so the bar fills as spend accrues.
    """

    def __init__(self) -> None:
        """Build with the recorder's phase labels."""
        super().__init__(_RECORD_PHASE_LABELS)

    def cost_update(self, spent_usd: float, max_usd: float) -> None:
        """Drive the synthetic ``COST`` task off live spend.

        The Rich :class:`~rich.progress.Progress` widget accepts float totals,
        so the bar can render fractional spend without rounding. Lazy-create
        the task on first call (``start_phase`` is keyed on ``RecordPhase``,
        not the bar's float-vs-int total) and update it on every subsequent
        call.

        ``ThickBarColumn`` and ``OptionalMofNCompleteColumn`` both treat
        ``total <= 1`` as "single-shot, pulse instead of fill" — that branch
        suppresses both the bar and the ``M/N`` cell. To keep the spend meter
        rendering as a filled bar even when the cap is exactly 1.0 USD, set
        ``total`` to ``max(max_usd, 1.001)``: the columns see a ``> 1`` total
        and fill the bar; the visible numeric overshoot under the rare exact-1
        cap is acceptable next to losing the bar entirely.
        """
        bar_total = max(max_usd, 1.001)
        if RecordPhase.COST not in self._tasks:
            # ``add_task`` types ``completed`` as ``int``; seed at zero and let
            # the immediate ``update`` push the float ``spent_usd`` through.
            self._tasks[RecordPhase.COST] = self._progress.add_task(
                self._label(RecordPhase.COST),
                total=bar_total,
            )
        self._progress.update(
            self._tasks[RecordPhase.COST],
            total=bar_total,
            completed=spent_usd,
        )


# Public (no leading underscore) because it's imported cross-module from
# slopmortem.evals.runner; see CLAUDE.md "no `# pyright: ignore` shortcuts".
def render_record_footer(  # noqa: PLR0913 — footer pulls every stat from the runner
    console: Console,
    *,
    total_cost_usd: float,
    max_cost_usd: float,
    rows_total: int,
    rows_succeeded: int,
    cassettes_written: int,
) -> None:
    """Print a summary panel after a recording run; mirrors ``_render_query_footer``."""
    parts = [
        f"cost=${total_cost_usd:.4f}/${max_cost_usd:.4f}",
        f"rows={rows_succeeded}/{rows_total}",
        f"cassettes={cassettes_written}",
    ]
    console.print(
        Panel(
            " • ".join(parts),
            title="[bold cyan]done[/bold cyan]",
            title_align="left",
            border_style="cyan",
            expand=False,
        )
    )
