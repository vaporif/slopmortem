"""Top-level CLI: ``query``, ``replay``, and ``embed-prefetch`` subcommands.

``query`` is the production entry point for the synthesis pipeline. Loads
:class:`Config`, starts Laminar tracing (gated on the endpoint guard in
:mod:`slopmortem.tracing` plus an env-var API key), builds the real OpenRouter
LLM client, the OpenAI embedding client, and the Qdrant corpus, then dispatches
to :func:`slopmortem.pipeline.run_query`. Stage progress goes to stderr
(TTY-gated); the rendered Markdown report goes to stdout.

``replay`` iterates an evals dataset (JSONL, one InputContext per line). The
missing-dataset path exits with code 2 so CI smoke tests can probe the wiring
without a fixture corpus.

``ingest`` lives in :mod:`slopmortem.cli._ingest_cmd`; it shares ``app`` and the
``_maybe_init_tracing`` helper with this module via side-effect import in
:mod:`slopmortem.cli`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

# Silence gRPC C-Core's INFO log channel BEFORE the Laminar import below pulls
# in grpcio. Without this, the OTLP exporter's pool prints
# ``ev_poll_posix.cc:593 FD from fork parent still in poll list`` on every
# poll-loop wake-up, which interleaves with the Rich progress redraws and turns
# the terminal into a glog smear. ``ERROR`` keeps real failures visible while
# killing the chatty INFO/WARNING traffic.
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("GRPC_TRACE", "")
os.environ.setdefault("GLOG_minloglevel", "2")

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

logger = logging.getLogger(__name__)

app = typer.Typer(
    add_completion=False,
    help="slopmortem: query and ingest startup post-mortems.",
)

# Names re-exported for the sibling subcommand modules (``_ingest_cmd``,
# ``_query_cmd``, ``_replay_cmd``). T3.3 will move these helpers to
# ``slopmortem.cli._common``; until then ``__all__`` declares the cross-module
# surface so basedpyright doesn't flag the in-module helpers as unused.
__all__ = [
    "_QUERY_PHASE_LABELS",
    "RichQueryProgress",
    "_build_deps",
    "_maybe_init_tracing",
    "_render_query_footer",
    "app",
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
    """Construct production LLM, embedder, corpus, and budget from *config*.

    A helper so CLI smoke tests can monkeypatch one symbol. All credentials and
    connection settings come from :class:`Config`, which pydantic-settings
    populates from env vars, ``.env``, and TOML.
    """
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
    """Rich-backed :class:`slopmortem.pipeline.QueryProgress` impl."""

    def __init__(self) -> None:
        super().__init__(_QUERY_PHASE_LABELS)


def _render_query_footer(console: Console, report: Report) -> None:
    """Print a summary panel to *console* after a query run."""
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


if __name__ == "__main__":
    app()
