# ruff: noqa: FBT002 - typer signatures are bool flags with False defaults by convention.
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

import contextlib
import functools
import json
import logging
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

# Silence gRPC C-Core's INFO log channel BEFORE the Laminar import below pulls
# in grpcio. Without this, the OTLP exporter's pool prints
# ``ev_poll_posix.cc:593 FD from fork parent still in poll list`` on every
# poll-loop wake-up, which interleaves with the Rich progress redraws and turns
# the terminal into a glog smear. ``ERROR`` keeps real failures visible while
# killing the chatty INFO/WARNING traffic.
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("GRPC_TRACE", "")
os.environ.setdefault("GLOG_minloglevel", "2")

import anyio  # must follow the GRPC env-var setup above
import typer
from lmnr import Laminar, observe
from openai import AsyncOpenAI
from rich.console import Console
from rich.panel import Panel

from slopmortem.budget import Budget
from slopmortem.cli_progress import RichPhaseProgress
from slopmortem.config import load_config
from slopmortem.corpus import (
    QdrantCorpus,
    set_query_corpus,
)
from slopmortem.llm import FastEmbedEmbeddingClient, OpenRouterClient, make_embedder
from slopmortem.models import InputContext
from slopmortem.pipeline import QueryPhase, cutoff_iso, run_query
from slopmortem.render import render
from slopmortem.stages import extract_facets, retrieve
from slopmortem.tracing import init_tracing

if TYPE_CHECKING:
    from slopmortem.config import Config
    from slopmortem.corpus import Corpus
    from slopmortem.llm import EmbeddingClient, LLMClient
    from slopmortem.models import Report

logger = logging.getLogger(__name__)

app = typer.Typer(
    add_completion=False,
    help="slopmortem: query and ingest startup post-mortems.",
)


_RUNS_DIR = Path(".slopmortem/runs")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SLUG_MAX = 40

_NOT_FOUND_MARKDOWN = """# No matching post-mortems found

The query returned no synthesized candidates above the similarity floor.
Try broadening the description or removing the `--years` filter."""


def _slugify(text: str) -> str:
    """Lowercase, collapse non-alphanumerics to ``-``, truncate to 40 chars."""
    s = _SLUG_RE.sub("-", text.lower()).strip("-")
    return s[:_SLUG_MAX].rstrip("-") or "run"


def _query_run_path(ctx: InputContext) -> Path:
    """Return ``.slopmortem/runs/<utc-ts>-<slug>.md`` for this run."""
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base = ctx.name if ctx.name and ctx.name != "(unnamed)" else ctx.description
    return _RUNS_DIR / f"{ts}-{_slugify(base)}.md"


@app.command("query")
def query_cmd(
    description: Annotated[
        str,
        typer.Argument(help="The pitch text to analyze."),
    ],
    name: Annotated[
        str | None,
        typer.Option("--name", help="Optional name for the input pitch."),
    ] = None,
    years: Annotated[
        int | None,
        typer.Option(
            "--years",
            help="Only consider candidates whose failure_date falls within this many years.",
        ),
    ] = None,
    debug_retrieve: Annotated[
        bool,
        typer.Option(
            "--debug-retrieve",
            help=(
                "Run facet_extract + retrieve only; print the candidate list and exit. "
                "Skips rerank and synthesize so retrieval can be inspected in isolation."
            ),
        ),
    ] = False,
    to_stdout: Annotated[
        bool,
        typer.Option(
            "--stdout",
            help=(
                "Print the rendered report to stdout instead of writing it under "
                ".slopmortem/runs/. Use when piping the report into another tool."
            ),
        ),
    ] = False,
) -> None:
    """Run the synthesis pipeline against *description* and persist a Markdown report.

    By default the rendered :class:`Report` is written to
    ``.slopmortem/runs/<utc-timestamp>-<slug>.md`` and only the path is echoed
    to stdout. Pass ``--stdout`` to dump the report to stdout instead (handy in
    shell pipelines). When no candidates clear the similarity floor, a short
    "not found" message goes to stdout and the command exits with code 1 (no
    file is written). Stage progress streams to stderr (TTY-gated). Laminar
    tracing is gated on ``Config.enable_tracing`` and a present
    ``LMNR_PROJECT_API_KEY``; if the key is missing but tracing is enabled, a
    one-line warning goes to stderr and the run continues untraced.
    """
    anyio.run(
        functools.partial(
            _query,
            description=description,
            name=name,
            years=years,
            debug_retrieve=debug_retrieve,
            to_stdout=to_stdout,
        )
    )


@observe(name="cli.query")
async def _query(
    *,
    description: str,
    name: str | None,
    years: int | None,
    debug_retrieve: bool = False,
    to_stdout: bool = False,
) -> None:
    """Async impl for ``slopmortem query``. Wires production deps and dispatches."""
    config = load_config()
    _maybe_init_tracing(config)
    llm, embedder, corpus, budget = _build_deps(config)
    set_query_corpus(corpus)
    ctx = InputContext(name=name or "(unnamed)", description=description, years_filter=years)
    if debug_retrieve:
        await _debug_retrieve(ctx, llm=llm, embedder=embedder, corpus=corpus, config=config)
        return

    progress_ctx: contextlib.AbstractContextManager[RichQueryProgress | None] = (
        RichQueryProgress() if sys.stderr.isatty() else contextlib.nullcontext()
    )
    err_console = Console(stderr=True)
    try:
        with progress_ctx as bar:
            report = await run_query(
                ctx,
                llm=llm,
                embedding_client=embedder,
                corpus=corpus,
                config=config,
                budget=budget,
                progress=bar,
            )
    except KeyboardInterrupt:
        err_console.rule("[bold yellow]query cancelled (Ctrl-C)", style="yellow")
        raise
    except BaseException:
        err_console.rule("[bold red]query failed", style="red")
        err_console.print_exception(show_locals=False)
        raise

    if bar is not None:
        _render_query_footer(bar.console, report)

    if not report.candidates:
        typer.echo(_NOT_FOUND_MARKDOWN)
        raise typer.Exit(code=1)

    rendered = render(report)
    if to_stdout:
        typer.echo(rendered)
        return
    out_path = _query_run_path(ctx)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _ = out_path.write_text(rendered, encoding="utf-8")
    err_console.print(f"[bold green]Report saved to[/bold green] {out_path.resolve()}")
    if not sys.stdout.isatty():
        typer.echo(str(out_path))


_DEBUG_SUMMARY_MAX = 200


async def _debug_retrieve(
    ctx: InputContext,
    *,
    llm: LLMClient,
    embedder: EmbeddingClient,
    corpus: Corpus,
    config: Config,
) -> None:
    """Run facet_extract + retrieve and print the candidate list to stdout."""
    facets = await extract_facets(
        ctx.description,
        llm,
        model=config.model_facet,
        max_tokens=config.max_tokens_facet,
    )
    cutoff = cutoff_iso(ctx.years_filter)
    candidates = await retrieve(
        description=ctx.description,
        facets=facets,
        corpus=corpus,
        embedding_client=embedder,
        cutoff_iso=cutoff,
        strict_deaths=config.strict_deaths,
        k_retrieve=config.K_retrieve,
    )

    typer.echo(f"# debug-retrieve  input={ctx.name!r}  cutoff={cutoff or 'none'}")
    typer.echo(f"facets.closed: sector={facets.sector} business_model={facets.business_model}")
    typer.echo(f"               customer_type={facets.customer_type} geography={facets.geography}")
    typer.echo(f"               monetization={facets.monetization}")
    typer.echo(f"facets.open:   sub_sector={facets.sub_sector} product_type={facets.product_type}")
    typer.echo(f"               price_point={facets.price_point}")
    typer.echo(f"facets.years:  founding={facets.founding_year} failure={facets.failure_year}")
    typer.echo(f"retrieved: {len(candidates)} (k_retrieve={config.K_retrieve})")
    typer.echo("")
    for i, c in enumerate(candidates, start=1):
        p = c.payload
        founded = p.founding_date.isoformat() if p.founding_date else "?"
        failed = p.failure_date.isoformat() if p.failure_date else "?"
        summary = p.summary.replace("\n", " ").strip()
        if len(summary) > _DEBUG_SUMMARY_MAX:
            summary = summary[: _DEBUG_SUMMARY_MAX - 3] + "..."
        meta = f"{p.facets.sector}/{p.facets.business_model}, founded={founded}, failed={failed}"
        typer.echo(f"[{i}] score={c.score:.4f}  {p.name}  ({meta})")
        typer.echo(f"    id={c.canonical_id}  provenance={p.provenance}  slop={p.slop_score:.2f}")
        if c.alias_canonicals:
            typer.echo(f"    aliases: {', '.join(c.alias_canonicals)}")
        if p.sources:
            extra = f" +{len(p.sources) - 1}" if len(p.sources) > 1 else ""
            typer.echo(f"    sources: {p.sources[0]}{extra}")
        elif p.provenance_id:
            typer.echo(f"    provenance: {p.provenance_id}")
        typer.echo(f"    summary: {summary}")
        typer.echo("")


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


@app.command("replay")
def replay_cmd(
    dataset: Annotated[
        str,
        typer.Argument(help="Dataset name under tests/evals/datasets/."),
    ],
) -> None:
    """Replay a JSONL evals dataset through the synthesis pipeline.

    Each line parses into an :class:`InputContext`; ``run_query`` runs with the
    same dependency wiring as ``query``; the rendered :class:`Report` for each
    row goes to stdout. Dataset format: JSONL, one InputContext per line.
    """
    anyio.run(_replay, dataset)


@observe(name="cli.replay")
async def _replay(dataset: str) -> None:
    path = Path("tests/evals/datasets") / f"{dataset}.jsonl"
    if not path.exists():
        typer.echo(f"no dataset at {path}; run 'just eval-record' to generate it", err=True)
        raise typer.Exit(code=2)

    config = load_config()
    _maybe_init_tracing(config)
    llm, embedder, corpus, budget = _build_deps(config)
    set_query_corpus(corpus)

    progress_ctx: contextlib.AbstractContextManager[RichQueryProgress | None] = (
        RichQueryProgress() if sys.stderr.isatty() else contextlib.nullcontext()
    )
    with progress_ctx as bar:
        for raw_line in path.read_text().splitlines():
            line = raw_line.strip()
            if not line:
                continue
            # ``json.loads`` returns ``Any`` by design; the per-site ignore
            # narrows the unknown payload to ``object``.
            # ``InputContext.model_validate`` is the strict boundary.
            row: object = json.loads(line)  # pyright: ignore[reportAny]
            ctx = InputContext.model_validate(row)
            report = await run_query(
                ctx,
                llm=llm,
                embedding_client=embedder,
                corpus=corpus,
                config=config,
                budget=budget,
                progress=bar,
            )
            if bar is not None:
                _render_query_footer(bar.console, report)
            typer.echo(render(report))


@app.command("embed-prefetch")
def embed_prefetch_cmd() -> None:
    """Warm the configured embedder's model cache (useful for CI / first-run)."""
    anyio.run(_embed_prefetch)


async def _embed_prefetch() -> None:
    config = load_config()
    budget = Budget(cap_usd=0.0)
    embedder = make_embedder(config, budget)
    if not isinstance(embedder, FastEmbedEmbeddingClient):
        typer.echo(
            f"slopmortem: provider {config.embedding_provider!r} has no local cache to prefetch",
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        await embedder.prefetch()
    except Exception as exc:
        typer.echo(f"slopmortem: embed-prefetch failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"slopmortem: prefetched {config.embed_model_id} into the fastembed cache")


if __name__ == "__main__":
    app()
