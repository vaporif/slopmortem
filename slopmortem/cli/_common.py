"""Shared helpers used by 2+ subcommand modules.

Lives here so subcommand files can import without forming circular dependencies
through ``cli/__init__.py``. The leading underscore signals package-private;
the import-linter contract enforces it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer
from lmnr import Laminar
from openai import AsyncOpenAI
from rich.panel import Panel

from slopmortem.budget import Budget
from slopmortem.cli_progress import RichPhaseProgress
from slopmortem.corpus import QdrantCorpus
from slopmortem.llm import OpenRouterClient, make_embedder
from slopmortem.pipeline import QueryPhase
from slopmortem.tracing import init_tracing

if TYPE_CHECKING:
    from rich.console import Console

    from slopmortem.config import Config
    from slopmortem.corpus import Corpus
    from slopmortem.llm import EmbeddingClient, LLMClient
    from slopmortem.models import Report

# ``__all__`` flags these underscore-prefixed names as intentional package-private
# exports so basedpyright stops reporting reportPrivateUsage at the import sites
# in ``_*_cmd.py`` and ``slopmortem.evals.runner``.
__all__ = [
    "_QUERY_PHASE_LABELS",
    "RichQueryProgress",
    "_build_deps",
    "_maybe_init_tracing",
    "_render_query_footer",
]


def _maybe_init_tracing(config: Config) -> None:
    """Opt-in Laminar init gated on ``enable_tracing`` and a non-empty API key."""
    base_url = config.lmnr_base_url or None
    init_tracing(
        base_url=base_url,
        allow_remote=bool(config.lmnr_allow_remote),
    )
    if not config.enable_tracing:
        return
    api_key = config.lmnr_project_api_key.get_secret_value()
    if not api_key:
        typer.echo(
            "slopmortem: LMNR_PROJECT_API_KEY missing; tracing disabled",
            err=True,
        )
        return
    Laminar.initialize(project_api_key=api_key, base_url=base_url)


def _build_deps(
    config: Config,
) -> tuple[LLMClient, EmbeddingClient, Corpus, Budget]:
    """One symbol for CLI smoke tests to monkeypatch."""
    from qdrant_client import AsyncQdrantClient  # noqa: PLC0415 - heavy dep, lazy import

    budget = Budget(cap_usd=config.max_cost_usd_per_query)

    openrouter_sdk = AsyncOpenAI(
        api_key=config.openrouter_api_key.get_secret_value(),
        base_url=config.openrouter_base_url,
    )
    llm = OpenRouterClient(
        sdk=openrouter_sdk,
        budget=budget,
        model=config.model_synthesize,
    )

    embedder = make_embedder(config, budget)

    qdrant_client = AsyncQdrantClient(host=config.qdrant_host, port=config.qdrant_port)
    corpus = QdrantCorpus(
        client=qdrant_client,
        collection=config.qdrant_collection,
        post_mortems_root=Path(config.post_mortems_root),
        facet_boost=config.facet_boost,
        rrf_k=config.rrf_k,
    )

    return llm, embedder, corpus, budget


_QUERY_PHASE_LABELS: dict[QueryPhase, str] = {
    QueryPhase.FACET_EXTRACT: "Extracting facets",
    QueryPhase.RETRIEVE: "Retrieving candidates",
    QueryPhase.RERANK: "Reranking candidates",
    QueryPhase.SYNTHESIZE: "Synthesizing post-mortems",
}


class RichQueryProgress(RichPhaseProgress[QueryPhase]):
    def __init__(self) -> None:
        super().__init__(_QUERY_PHASE_LABELS)


def _render_query_footer(console: Console, report: Report) -> None:
    meta = report.pipeline_meta
    parts = [
        f"cost=${meta.cost_usd_total:.4f}",
        f"latency={meta.latency_ms_total}ms",
        f"synthesized={len(report.candidates)}",
    ]
    if meta.filtered_pre_synth > 0:
        parts.append(f"filtered_pre_synth={meta.filtered_pre_synth}")
    if meta.trace_id:
        parts.append(f"trace={meta.trace_id}")
    if meta.budget_exceeded:
        parts.append("[bold red]budget_exceeded[/bold red]")
    console.print(
        Panel(
            " • ".join(parts),
            title="[bold cyan]done[/bold cyan]",
            title_align="left",
            border_style="cyan",
            expand=False,
        )
    )
