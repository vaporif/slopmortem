"""Rich UI for the eval cassette recorder: phase progress + post-run footer panel.

:class:`RichRecordProgress` wraps the shared :class:`RichPhaseProgress` widget
with the recorder's phase labels. :func:`render_record_footer` mirrors the
shape of ``slopmortem.cli._render_query_footer`` — same border style, same
`` • ``-joined facts — so the recorder's done state reads consistently with
the query CLI. Live spend is surfaced via per-row log lines and the footer;
no dedicated bar.
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
}


class RichRecordProgress(RichPhaseProgress[RecordPhase]):
    """Rich-backed :class:`RecordProgress` impl shared by ``runner._run_record``."""

    def __init__(self) -> None:  # noqa: D107
        super().__init__(_RECORD_PHASE_LABELS)


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
