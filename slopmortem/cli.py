# ruff: noqa: FBT002 — typer signatures are bool flags with False defaults by convention.
"""Top-level CLI entry point. Currently exposes only ``slopmortem ingest``.

The full CLI surface (query, synthesize, list-review, reconcile, …) lands
with Task #10. This module ships the ``ingest`` subcommand and a Typer app
ready for later commands to register.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Annotated

import typer

logger = logging.getLogger(__name__)

app = typer.Typer(
    add_completion=False,
    help="slopmortem: query and ingest startup post-mortems.",
)


@app.command("ingest")
def ingest_cmd(  # noqa: PLR0913 — every flag mirrors the spec; user types kwargs.
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Count entries that would be ingested; write nothing.")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", help="Bypass the skip_key short-circuit.")
    ] = False,
    reconcile: Annotated[
        bool,
        typer.Option(
            "--reconcile",
            help="Run the reconcile pass and apply repairs for the six drift classes.",
        ),
    ] = False,
    reclassify: Annotated[
        bool,
        typer.Option(
            "--reclassify",
            help=(
                "Re-run the slop classifier on quarantined docs; "
                "if no longer slop, route through entity resolution."
            ),
        ),
    ] = False,
    list_review: Annotated[
        bool,
        typer.Option(
            "--list-review",
            help="Print the pending_review queue and exit.",
        ),
    ] = False,
    crunchbase_csv: Annotated[
        Path | None,
        typer.Option(
            "--crunchbase-csv",
            help="Path to a Crunchbase CSV; enables the Crunchbase adapter for this run.",
        ),
    ] = None,
    enrich_wayback: Annotated[
        bool, typer.Option("--enrich-wayback", help="Enable the Wayback enricher.")
    ] = False,
    tavily_enrich: Annotated[
        bool, typer.Option("--tavily-enrich", help="Enable the Tavily enricher.")
    ] = False,
    post_mortems_root: Annotated[
        Path,
        typer.Option(
            "--post-mortems-root",
            help="Root for raw/, canonical/, quarantine/ trees.",
        ),
    ] = Path("./post_mortems"),
) -> None:
    """Run the ingest pipeline against the configured sources.

    Wires user flags into :func:`slopmortem.ingest.ingest`. Every config knob
    (slop_threshold, ingest_concurrency, embed_model_id, taxonomy_version,
    reliability_rank_version, etc.) comes from :func:`slopmortem.config.load_config`.
    """
    asyncio.run(
        _run_ingest(
            dry_run=dry_run,
            force=force,
            reconcile=reconcile,
            reclassify=reclassify,
            list_review=list_review,
            crunchbase_csv=crunchbase_csv,
            enrich_wayback=enrich_wayback,
            tavily_enrich=tavily_enrich,
            post_mortems_root=post_mortems_root,
        )
    )


async def _run_ingest(  # noqa: PLR0913 — the ingest CLI surface is wide.
    *,
    dry_run: bool,
    force: bool,
    reconcile: bool,
    reclassify: bool,
    list_review: bool,
    crunchbase_csv: Path | None,
    enrich_wayback: bool,
    tavily_enrich: bool,
    post_mortems_root: Path,
) -> None:
    """Async impl behind ``slopmortem ingest``. Resolves wiring then dispatches."""
    # Concrete production wiring (sources, qdrant client, embedder, llm, classifier)
    # arrives in Task #10; for v1 5b the CLI is a thin shim. Surface what flag
    # combinations the operator chose so they show up in the run log.
    flags = {
        "dry_run": dry_run,
        "force": force,
        "reconcile": reconcile,
        "reclassify": reclassify,
        "list_review": list_review,
        "crunchbase_csv": str(crunchbase_csv) if crunchbase_csv else None,
        "enrich_wayback": enrich_wayback,
        "tavily_enrich": tavily_enrich,
        "post_mortems_root": str(post_mortems_root),
    }
    logger.info("slopmortem ingest invoked: %s", flags)
    typer.echo(f"slopmortem ingest invoked: {flags}")
    note = (
        "production wiring (sources/qdrant/openrouter/openai-embed) lands in Task #10; "
        "the orchestrator itself is exercised by the test suite."
    )
    typer.echo(f"{note} flags={flags}")


if __name__ == "__main__":
    app()
