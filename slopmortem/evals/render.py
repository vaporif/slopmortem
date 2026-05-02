"""Rich UI for the eval cassette recorder.

:func:`render_record_footer` mirrors ``slopmortem.cli._render_query_footer``
so recorder and query CLI report consistently.
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

    def __init__(self) -> None:
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
