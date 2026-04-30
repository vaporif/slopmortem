# ruff: noqa: FBT002 — typer signatures are bool flags with False defaults by convention.
"""Top-level CLI entry point: ``slopmortem ingest``, ``query``, and ``replay``.

The ``query`` command is the production entry point for the synthesis pipeline.
It loads :class:`Config`, initializes Laminar tracing (gated on the endpoint
guard in :mod:`slopmortem.tracing` plus an env-var API key), constructs the
real OpenRouter LLM client, the OpenAI embedding client, and the Qdrant corpus,
then dispatches to :func:`slopmortem.pipeline.run_query`. Stage progress goes
to stderr (TTY-gated); the rendered Markdown report goes to stdout.

The ``replay`` command iterates an evals dataset (Task 11 ships the dataset
format and content). The missing-dataset path exits with code 2 so CI smoke
tests can probe the wiring without a fixture corpus.

The ``ingest`` command assembles real :class:`Source` / :class:`Enricher`
/ :class:`MergeJournal` / :class:`Corpus` / :class:`LLMClient` /
:class:`EmbeddingClient` / :class:`SlopClassifier` instances from
:class:`Config` and env vars and dispatches to
:func:`slopmortem.ingest.ingest`. ``--list-review`` is a read-only path that
queries :class:`MergeJournal` for the pending entity-resolution review queue
and prints it to stdout. ``--reconcile`` runs the six-drift-class scan with
``repair=True`` and prints the report. ``--reclassify`` re-runs the slop
classifier against every quarantine row and routes survivors back out of
the quarantine tree; ``--tavily-enrich`` appends a :class:`TavilyEnricher`
to the enrichers list.
"""

from __future__ import annotations

import contextlib
import functools
import json
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Self, cast

# Silence gRPC C-Core's INFO-level log channel BEFORE the Laminar import below
# transitively pulls in grpcio. Without this, the OTLP exporter's connection
# pool prints ``ev_poll_posix.cc:593 FD from fork parent still in poll list``
# on every poll-loop wake-up, which interleaves with the Rich progress redraws
# and turns the terminal into a smear of glog noise. ``ERROR`` keeps real
# failures visible while killing the chatty INFO/WARNING traffic.
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("GRPC_TRACE", "")
os.environ.setdefault("GLOG_minloglevel", "2")

import anyio  # must follow the GRPC env-var setup above
import typer
from lmnr import Laminar, observe
from openai import AsyncOpenAI
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

from slopmortem.budget import Budget
from slopmortem.config import load_config
from slopmortem.corpus.qdrant_store import QdrantCorpus
from slopmortem.corpus.reclassify import reclassify_quarantined
from slopmortem.corpus.reconcile import reconcile
from slopmortem.corpus.sources.crunchbase_csv import CrunchbaseCsvSource
from slopmortem.corpus.sources.curated import CuratedSource
from slopmortem.corpus.sources.hn_algolia import HNAlgoliaSource
from slopmortem.corpus.sources.tavily import TavilyEnricher
from slopmortem.corpus.sources.wayback import WaybackEnricher
from slopmortem.corpus.tools_impl import _set_corpus
from slopmortem.ingest import IngestPhase, IngestResult, ingest
from slopmortem.llm.fastembed_client import FastEmbedEmbeddingClient
from slopmortem.llm.openai_embeddings import OpenAIEmbeddingClient
from slopmortem.llm.openrouter import OpenRouterClient
from slopmortem.models import InputContext
from slopmortem.pipeline import QueryPhase, cutoff_iso, run_query
from slopmortem.render import render
from slopmortem.stages.facet_extract import extract_facets
from slopmortem.stages.retrieve import retrieve
from slopmortem.tracing import init_tracing

if TYPE_CHECKING:
    from types import TracebackType

    from slopmortem.config import Config
    from slopmortem.corpus.merge import MergeJournal
    from slopmortem.corpus.sources.base import Enricher, Source
    from slopmortem.corpus.store import Corpus
    from slopmortem.ingest import Corpus as IngestCorpus
    from slopmortem.ingest import SlopClassifier
    from slopmortem.llm.client import LLMClient
    from slopmortem.llm.embedding_client import EmbeddingClient
    from slopmortem.models import Report

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
    reconcile_flag: Annotated[
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
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit",
            help=(
                "Process only the first N entries after gathering from all sources. "
                "Source order is curated -> HN -> Crunchbase. Useful for cheap live "
                "smoke tests; cost scales with N."
            ),
        ),
    ] = None,
) -> None:
    """Run the ingest pipeline against the configured sources.

    Wires user flags into :func:`slopmortem.ingest.ingest`. Config knobs
    (slop_threshold, ingest_concurrency, embed_model_id, taxonomy_version,
    reliability_rank_version, etc.) come from :func:`slopmortem.config.load_config`.
    """
    anyio.run(
        functools.partial(
            _run_ingest,
            dry_run=dry_run,
            force=force,
            reconcile_flag=reconcile_flag,
            reclassify=reclassify,
            list_review=list_review,
            crunchbase_csv=crunchbase_csv,
            enrich_wayback=enrich_wayback,
            tavily_enrich=tavily_enrich,
            post_mortems_root=post_mortems_root,
            limit=limit,
        )
    )


async def _run_reclassify(config: Config, post_mortems_root: Path) -> None:
    """Re-run the slop classifier across quarantined rows and print the summary."""
    journal = await _build_journal(config, post_mortems_root)
    # reclassify is a live operation that must hit the slop judge for real;
    # construct only the LLM (no embedder/qdrant) and route it into the
    # Haiku-backed classifier.
    budget = Budget(cap_usd=config.max_cost_usd_per_ingest)
    openrouter_sdk = AsyncOpenAI(
        api_key=config.openrouter_api_key.get_secret_value(),
        base_url=config.openrouter_base_url,
    )
    llm = OpenRouterClient(sdk=openrouter_sdk, budget=budget, model=config.model_summarize)
    classifier = _build_slop_classifier(
        dry_run=False,
        llm=llm,
        model=config.model_summarize,
        max_tokens=config.max_tokens_slop_judge,
    )
    report = await reclassify_quarantined(
        journal=journal,
        slop_classifier=classifier,
        post_mortems_root=post_mortems_root,
        slop_threshold=config.slop_threshold,
    )
    counts = f"total={report.total} declassified={report.declassified}"
    tail = f"still_slop={report.still_slop} errors={report.errors}"
    typer.echo(f"reclassify: {counts} {tail}")


async def _run_reconcile(config: Config, post_mortems_root: Path) -> None:
    """Run the six-drift-class scan with ``repair=True`` and print the report."""
    journal = await _build_journal(config, post_mortems_root)
    corpus = await _build_ingest_corpus(config, post_mortems_root)
    report = await reconcile(
        journal=journal,
        corpus=corpus,
        post_mortems_root=post_mortems_root,
        repair=True,
    )
    typer.echo(f"reconcile: {len(report.rows)} drift findings, {len(report.applied)} repaired")
    for r in report.rows:
        typer.echo(f"  drift_class={r.drift_class}\t{r.path}\t{r.detail}")


@observe(name="cli.ingest")
async def _run_ingest(  # noqa: PLR0913, C901 — the ingest CLI surface is wide.
    *,
    dry_run: bool,
    force: bool,
    reconcile_flag: bool,
    reclassify: bool,
    list_review: bool,
    limit: int | None,
    crunchbase_csv: Path | None,
    enrich_wayback: bool,
    tavily_enrich: bool,
    post_mortems_root: Path,
) -> None:
    """Async impl behind ``slopmortem ingest``. Resolves wiring then dispatches."""
    config = load_config()
    _maybe_init_tracing(config)

    # Read-only short-circuits are wired before the full LLM/embedder build so
    # they don't require ``OPENROUTER_API_KEY`` / ``OPENAI_API_KEY``.
    if list_review:
        journal = await _build_journal(config, post_mortems_root)
        rows = await journal.list_pending_review()
        if not rows:
            typer.echo("(no pending_review rows)")
        for r in rows:
            score = r.similarity_score
            decision = r.haiku_decision
            rationale = r.haiku_rationale
            typer.echo(f"{r.pair_key}\tscore={score}\tdecision={decision}\trationale={rationale}")
        return

    # Spec: §Quarantine and reclassify line 252.
    if reclassify:
        await _run_reclassify(config, post_mortems_root)
        return

    # Spec: §Atomicity / six drift classes (a-f).
    if reconcile_flag:
        await _run_reconcile(config, post_mortems_root)
        return

    llm, embedder, corpus, budget, journal, classifier = await _build_ingest_deps(
        config, post_mortems_root, dry_run=dry_run
    )
    # The ingest-side Corpus Protocol is wider than the query-side one used by
    # ``_set_corpus``; the underlying ``QdrantCorpus`` instance satisfies both.
    _set_corpus(cast("Corpus", corpus))

    sources: list[Source] = [
        CuratedSource(yaml_path=_default_curated_yaml(), rps=3.0),
        HNAlgoliaSource(query="post-mortem", rps=5.0),
    ]
    if crunchbase_csv is not None:
        sources.append(CrunchbaseCsvSource(csv_path=crunchbase_csv))

    enrichers: list[Enricher] = []
    if enrich_wayback:
        enrichers.append(WaybackEnricher(rps=5.0))
    if tavily_enrich:
        enrichers.append(TavilyEnricher())

    # TTY-gated: only attach the Rich progress display when stderr is a real
    # terminal. Piped invocations (CI, ``> file``) get a quiet run.
    progress_ctx: contextlib.AbstractContextManager[RichIngestProgress | None] = (
        RichIngestProgress() if sys.stderr.isatty() else contextlib.nullcontext()
    )
    # Catch + print any exception that escapes the orchestrator BEFORE the
    # Rich live render tears down. Without this, ``Progress.__exit__`` clears
    # the screen and the traceback can interleave invisibly with bar redraws —
    # exactly what happened with the Wesabe / "collection doesn't exist" run.
    err_console = Console(stderr=True)
    try:
        with progress_ctx as bar:
            result = await ingest(
                sources=sources,
                enrichers=enrichers,
                journal=journal,
                corpus=corpus,
                llm=llm,
                embed_client=embedder,
                budget=budget,
                slop_classifier=classifier,
                config=config,
                post_mortems_root=post_mortems_root,
                dry_run=dry_run,
                force=force,
                limit=limit,
                progress=bar,
            )
    except KeyboardInterrupt:
        # Ctrl-C is a ``BaseException``; without surfacing it explicitly the
        # user just sees the prompt return with no signal that the run stopped.
        err_console.rule("[bold yellow]ingest cancelled (Ctrl-C)", style="yellow")
        raise
    except BaseException:
        # Catch ``BaseException`` (asyncio.CancelledError, SystemExit, etc.) too —
        # ``except Exception`` alone misses these and they exit silently when
        # Rich's live render tears down. We print + re-raise so the caller still
        # sees a non-zero exit; this is purely for visibility.
        err_console.rule("[bold red]ingest failed", style="red")
        err_console.print_exception(show_locals=False)
        raise
    if bar is not None:
        _render_ingest_result(bar.console, result)
    else:
        typer.echo(f"slopmortem ingest result: {result}")


def _default_curated_yaml() -> Path:
    """Return the in-tree curated post-mortem YAML path shipped with the package."""
    return Path(__file__).parent / "corpus" / "sources" / "curated" / "post_mortems_v0.yml"


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------


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
) -> None:
    """Run the synthesis pipeline against *description* and print a Markdown report.

    Streams stage progress to stderr (TTY-gated) while the pipeline runs; emits
    the rendered :class:`Report` to stdout when done. Tracing wiring (Laminar)
    is gated on ``Config.enable_tracing`` plus a present ``LMNR_PROJECT_API_KEY``.
    When the API key is missing but tracing is enabled, a one-line warning goes
    to stderr and the run continues without tracing.
    """
    anyio.run(
        functools.partial(
            _query,
            description=description,
            name=name,
            years=years,
            debug_retrieve=debug_retrieve,
        )
    )


@observe(name="cli.query")
async def _query(
    *,
    description: str,
    name: str | None,
    years: int | None,
    debug_retrieve: bool = False,
) -> None:
    """Async impl for ``slopmortem query``. Wires production deps and dispatches."""
    config = load_config()
    _maybe_init_tracing(config)
    llm, embedder, corpus, budget = _build_deps(config)
    _set_corpus(corpus)
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
        _render_query_footer(bar.console, report, n_target=config.N_synthesize)
    typer.echo(render(report))


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
        typer.echo(f"    summary: {summary}")
        typer.echo("")


def _maybe_init_tracing(config: Config) -> None:
    """Run the endpoint guard, then conditionally call ``Laminar.initialize``.

    Always runs the SSRF-style endpoint guard (a no-op when ``LMNR_BASE_URL``
    is unset). If tracing is enabled in config but the project API key is
    missing, log to stderr and skip Laminar init. Tracing is best-effort.
    """
    base_url = config.lmnr_base_url or None
    init_tracing(
        base_url=base_url,
        allow_remote=bool(config.lmnr_allow_remote),
    )
    if not config.enable_tracing:
        return
    api_key = config.lmnr_project_api_key.get_secret_value()
    if not api_key:
        print(  # noqa: T201 — CLI surface; intentional stderr write
            "slopmortem: LMNR_PROJECT_API_KEY missing; tracing disabled",
            file=sys.stderr,
        )
        return
    Laminar.initialize(project_api_key=api_key, base_url=base_url)


def _make_embedder(config: Config, budget: Budget) -> EmbeddingClient:
    """Construct the configured embedder; branch on ``config.embedding_provider``.

    Unknown provider names raise ``ValueError`` listing the supported values so
    a typo in ``slopmortem.toml`` fails loud at startup rather than mid-pipeline.
    """
    provider = config.embedding_provider
    if provider == "fastembed":
        return FastEmbedEmbeddingClient(
            model=config.embed_model_id,
            budget=budget,
            cache_dir=config.embed_cache_dir,
        )
    if provider == "openai":
        openai_sdk = AsyncOpenAI(
            api_key=config.openai_api_key.get_secret_value(),
        )
        return OpenAIEmbeddingClient(
            sdk=openai_sdk,
            budget=budget,
            model=config.embed_model_id,
        )
    valid = ("fastembed", "openai")
    msg = f"unknown embedding_provider {provider!r}; valid choices: {valid}"
    raise ValueError(msg)


def _build_deps(
    config: Config,
) -> tuple[LLMClient, EmbeddingClient, Corpus, Budget]:
    """Construct production LLM, embedder, corpus, and budget from *config*.

    Factored out as a helper so CLI smoke tests can monkeypatch a single symbol.
    All credentials and connection settings come from :class:`Config`, which
    pydantic-settings populates from env vars, ``.env``, and TOML.
    """
    from qdrant_client import AsyncQdrantClient  # noqa: PLC0415 — heavy dep, lazy import

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

    embedder = _make_embedder(config, budget)

    qdrant_client = AsyncQdrantClient(host=config.qdrant_host, port=config.qdrant_port)
    corpus = QdrantCorpus(
        client=qdrant_client,
        collection=config.qdrant_collection,
        post_mortems_root=Path(config.post_mortems_root),
        facet_boost=config.facet_boost,
        rrf_k=config.rrf_k,
    )

    return llm, embedder, corpus, budget


async def _build_journal(config: Config, post_mortems_root: Path) -> MergeJournal:
    """Construct and schema-initialize the merge journal.

    ``init()`` is idempotent (``CREATE TABLE IF NOT EXISTS``), so calling it
    on every CLI invocation is cheap and ensures fresh dev databases work
    without an explicit setup step.
    """
    from slopmortem.corpus.merge import MergeJournal  # noqa: PLC0415

    journal_path = Path(
        config.merge_journal_path or str(post_mortems_root.parent / "journal.sqlite")
    )
    journal = MergeJournal(journal_path)
    await journal.init()
    return journal


def _build_slop_classifier(
    *, dry_run: bool, llm: LLMClient, model: str, max_tokens: int | None = None
) -> SlopClassifier:
    """Construct the slop classifier.

    Dry-run uses :class:`FakeSlopClassifier` so the run requires no API key
    and emits no LLM cost. Live ingest uses :class:`HaikuSlopClassifier`,
    which sends one Haiku call per entry and quarantines anything that
    isn't a specific dead-company narrative.
    """
    if dry_run:
        from slopmortem.ingest import FakeSlopClassifier  # noqa: PLC0415

        return FakeSlopClassifier()
    from slopmortem.ingest import HaikuSlopClassifier  # noqa: PLC0415

    return HaikuSlopClassifier(llm=llm, model=model, max_tokens=max_tokens)


async def _build_ingest_corpus(config: Config, post_mortems_root: Path) -> IngestCorpus:
    """Construct the Qdrant-backed ingest corpus and ensure its collection exists.

    ``ensure_collection`` is idempotent (creates only when missing), so calling
    it on every invocation is cheap and means a fresh dev box doesn't need a
    separate setup step. Without this, ``upsert_chunk`` fails with
    ``Collection 'slopmortem' doesn't exist`` on the first write.
    """
    from qdrant_client import AsyncQdrantClient  # noqa: PLC0415 — heavy dep, lazy import

    from slopmortem.corpus.qdrant_store import ensure_collection  # noqa: PLC0415
    from slopmortem.llm.openai_embeddings import EMBED_DIMS  # noqa: PLC0415

    if config.embed_model_id not in EMBED_DIMS:
        msg = f"unknown embed model {config.embed_model_id!r}; add it to EMBED_DIMS"
        raise ValueError(msg)
    dim = EMBED_DIMS[config.embed_model_id]

    qdrant_client = AsyncQdrantClient(host=config.qdrant_host, port=config.qdrant_port)
    await ensure_collection(qdrant_client, config.qdrant_collection, dim=dim)
    corpus = QdrantCorpus(
        client=qdrant_client,
        collection=config.qdrant_collection,
        post_mortems_root=post_mortems_root,
        facet_boost=config.facet_boost,
        rrf_k=config.rrf_k,
    )
    # ``QdrantCorpus`` ships ``upsert_chunk`` but not yet ``has_chunks`` /
    # ``delete_chunks_for_canonical`` (production gap tracked separately);
    # cast at this boundary so the CLI surface compiles against the strict
    # ingest-side ``Corpus`` Protocol declared in :mod:`slopmortem.ingest`.
    return cast("IngestCorpus", corpus)


async def _build_ingest_deps(
    config: Config,
    post_mortems_root: Path,
    *,
    dry_run: bool,
) -> tuple[LLMClient, EmbeddingClient, IngestCorpus, Budget, MergeJournal, SlopClassifier]:
    """Construct the full ingest-side wiring: LLM / embed / corpus / budget / journal / classifier.

    Mirrors :func:`_build_deps` but uses ``max_cost_usd_per_ingest`` for the
    budget cap and additionally constructs a :class:`MergeJournal` and the
    slop classifier. ``dry_run=True`` swaps in :class:`FakeSlopClassifier`
    so the run needs no real API key.
    """
    budget = Budget(cap_usd=config.max_cost_usd_per_ingest)

    openrouter_sdk = AsyncOpenAI(
        api_key=config.openrouter_api_key.get_secret_value(),
        base_url=config.openrouter_base_url,
    )
    llm = OpenRouterClient(
        sdk=openrouter_sdk,
        budget=budget,
        model=config.model_facet,  # ingest uses the cheap model
    )

    embedder = _make_embedder(config, budget)

    corpus = await _build_ingest_corpus(config, post_mortems_root)
    journal = await _build_journal(config, post_mortems_root)
    classifier = _build_slop_classifier(
        dry_run=dry_run,
        llm=llm,
        model=config.model_summarize,
        max_tokens=config.max_tokens_slop_judge,
    )
    return llm, embedder, corpus, budget, journal, classifier


# ---------------------------------------------------------------------------
# ingest progress (Rich)
# ---------------------------------------------------------------------------

# Phase labels keyed on the IngestPhase enum so any new phase added in
# ``slopmortem.ingest`` flags here at type-check time as a missing label.
_INGEST_PHASE_LABELS: dict[IngestPhase, str] = {
    IngestPhase.GATHER: "Gathering entries from sources",
    IngestPhase.CLASSIFY: "Classifying / slop-filtering",
    IngestPhase.CACHE_WARM: "Warming prompt cache",
    IngestPhase.FAN_OUT: "Facets + summarize fan-out",
    IngestPhase.WRITE: "Entity-resolve / chunk / qdrant",
}


class RichIngestProgress:
    """Rich-backed :class:`slopmortem.ingest.IngestProgress` impl.

    Holds one :class:`rich.progress.Progress` instance with a task per phase.
    Task IDs are created lazily on ``start_phase`` so phases the run skips
    (e.g. cache_warm in dry-run) don't appear as empty bars. ``log`` writes a
    grey one-liner above the bars via the same console, so messages don't
    fight the progress redraw.
    """

    def __init__(self) -> None:
        """Build the underlying ``Progress`` and console; tasks are added lazily."""
        self._console = Console(stderr=True)
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}", justify="left"),
            BarColumn(
                bar_width=None,
                style="grey50",
                complete_style="cyan",
                finished_style="green",
                pulse_style="cyan",
            ),
            MofNCompleteColumn(),
            TextColumn("[dim]•"),
            TimeElapsedColumn(),
            TextColumn("[dim]eta"),
            TimeRemainingColumn(),
            console=self._console,
            transient=False,
        )
        self._tasks: dict[IngestPhase, TaskID] = {}
        self._phase_errors: dict[IngestPhase, int] = {}

    def __enter__(self) -> Self:
        """Start the live render."""
        self._progress.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Tear down the live render."""
        self._progress.__exit__(exc_type, exc_val, exc_tb)

    @property
    def console(self) -> Console:
        """Underlying Rich console; the CLI uses it for the post-run table."""
        return self._console

    def _label(self, phase: IngestPhase) -> str:
        styled = f"[bold cyan]{_INGEST_PHASE_LABELS[phase]}[/bold cyan]"
        n = self._phase_errors.get(phase, 0)
        if not n:
            return styled
        noun = "error" if n == 1 else "errors"
        return f"{styled} [bold red]({n} {noun})[/bold red]"

    def start_phase(self, phase: IngestPhase, total: int | None) -> None:
        """Create or reset the bar for *phase* with the expected ``total``.

        ``total=None`` -> indeterminate (Rich pulses the bar; ETA blank).
        Used by ``GATHER`` when no ``--limit`` caps the run.
        """
        if phase in self._tasks:
            self._progress.reset(self._tasks[phase], total=total)
            self._progress.update(self._tasks[phase], description=self._label(phase))
            return
        self._tasks[phase] = self._progress.add_task(self._label(phase), total=total)

    def advance_phase(self, phase: IngestPhase, n: int = 1) -> None:
        """Move *phase*'s bar forward by ``n`` (no-op for unknown phases)."""
        tid = self._tasks.get(phase)
        if tid is not None:
            self._progress.advance(tid, n)

    def end_phase(self, phase: IngestPhase) -> None:
        """Complete *phase*'s bar and stop its spinner.

        For indeterminate phases (``total is None``), freeze the bar by
        setting ``total = completed`` — otherwise Rich keeps it pulsing
        even after the work is done.
        """
        tid = self._tasks.get(phase)
        if tid is None:
            return
        task = self._progress.tasks[tid]
        if task.total is None:
            self._progress.update(
                tid,
                total=task.completed,
                completed=task.completed,
                description=self._label(phase),
            )
            return
        self._progress.update(
            tid, completed=task.total or task.completed, description=self._label(phase)
        )

    def log(self, message: str) -> None:
        """Write a one-off neutral status line above the progress display."""
        self._console.log(message)

    def error(self, phase: IngestPhase, message: str) -> None:
        """Bump the error count for *phase* and log a red line above the bars."""
        self._phase_errors[phase] = self._phase_errors.get(phase, 0) + 1
        tid = self._tasks.get(phase)
        if tid is not None:
            self._progress.update(tid, description=self._label(phase))
        self._console.log(f"[bold red]ERROR[/bold red] [{phase.value}] {message}")


def _render_ingest_result(console: Console, result: IngestResult) -> None:
    """Print ``IngestResult`` as a two-column Rich table on *console*."""
    rows: list[tuple[str, str]] = [
        ("seen", str(result.seen)),
        ("processed", str(result.processed)),
        ("would_process", str(result.would_process)),
        ("quarantined", str(result.quarantined)),
        ("skipped", str(result.skipped)),
        ("errors", str(result.errors)),
        ("source_failures", str(result.source_failures)),
        ("cache_warmed", str(result.cache_warmed)),
        ("dry_run", str(result.dry_run)),
    ]
    table = Table(title="Ingest result", show_header=False, expand=False)
    table.add_column("Field", style="bold cyan", no_wrap=True)
    table.add_column("Value", style="white")
    for k, v in rows:
        table.add_row(k, v)
    console.print(table)


# ---------------------------------------------------------------------------
# query progress (Rich)
# ---------------------------------------------------------------------------

_QUERY_PHASE_LABELS: dict[QueryPhase, str] = {
    QueryPhase.FACET_EXTRACT: "Extracting facets",
    QueryPhase.RETRIEVE: "Retrieving candidates",
    QueryPhase.RERANK: "Reranking candidates",
    QueryPhase.SYNTHESIZE: "Synthesizing post-mortems",
}


class RichQueryProgress:
    """Rich-backed :class:`slopmortem.pipeline.QueryProgress` impl.

    Mirrors :class:`RichIngestProgress`: one Rich ``Progress`` instance with a
    task per phase, lazy task creation so unreached phases don't render empty
    bars, and a red error-count badge appended to the description when
    synthesize fan-out has per-candidate failures.
    """

    def __init__(self) -> None:
        """Build the underlying ``Progress`` and console; tasks are added lazily."""
        self._console = Console(stderr=True)
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("{task.description}", justify="left"),
            BarColumn(
                bar_width=None,
                style="grey50",
                complete_style="cyan",
                finished_style="green",
                pulse_style="cyan",
            ),
            MofNCompleteColumn(),
            TextColumn("[dim]•"),
            TimeElapsedColumn(),
            TextColumn("[dim]eta"),
            TimeRemainingColumn(),
            console=self._console,
            transient=False,
        )
        self._tasks: dict[QueryPhase, TaskID] = {}
        self._phase_errors: dict[QueryPhase, int] = {}

    def __enter__(self) -> Self:
        """Start the live render."""
        self._progress.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Tear down the live render."""
        self._progress.__exit__(exc_type, exc_val, exc_tb)

    @property
    def console(self) -> Console:
        """Underlying Rich console; the CLI uses it for the post-run footer."""
        return self._console

    def _label(self, phase: QueryPhase) -> str:
        styled = f"[bold cyan]{_QUERY_PHASE_LABELS[phase]}[/bold cyan]"
        n = self._phase_errors.get(phase, 0)
        if not n:
            return styled
        noun = "error" if n == 1 else "errors"
        return f"{styled} [bold red]({n} {noun})[/bold red]"

    def start_phase(self, phase: QueryPhase, total: int) -> None:
        """Create or reset the bar for *phase* with the expected ``total``."""
        if phase in self._tasks:
            self._progress.reset(self._tasks[phase], total=total)
            self._progress.update(self._tasks[phase], description=self._label(phase))
            return
        self._tasks[phase] = self._progress.add_task(self._label(phase), total=max(total, 1))

    def advance_phase(self, phase: QueryPhase, n: int = 1) -> None:
        """Move *phase*'s bar forward by ``n`` (no-op for unknown phases)."""
        tid = self._tasks.get(phase)
        if tid is not None:
            self._progress.advance(tid, n)

    def end_phase(self, phase: QueryPhase) -> None:
        """Complete *phase*'s bar and stop its spinner."""
        tid = self._tasks.get(phase)
        if tid is None:
            return
        task = self._progress.tasks[tid]
        self._progress.update(
            tid, completed=task.total or task.completed, description=self._label(phase)
        )

    def log(self, message: str) -> None:
        """Write a one-off neutral status line above the progress display."""
        self._console.log(message)

    def error(self, phase: QueryPhase, message: str) -> None:
        """Bump the error count for *phase* and log a red line above the bars."""
        self._phase_errors[phase] = self._phase_errors.get(phase, 0) + 1
        tid = self._tasks.get(phase)
        if tid is not None:
            self._progress.update(tid, description=self._label(phase))
        self._console.log(f"[bold red]ERROR[/bold red] [{phase.value}] {message}")


def _render_query_footer(console: Console, report: Report, n_target: int) -> None:
    """Print a one-line summary footer to *console* after a query run."""
    meta = report.pipeline_meta
    parts = [
        f"cost=${meta.cost_usd_total:.4f}",
        f"latency={meta.latency_ms_total}ms",
        f"synthesized={len(report.candidates)}/{n_target}",
    ]
    if meta.trace_id:
        parts.append(f"trace={meta.trace_id}")
    if meta.budget_exceeded:
        parts.append("[bold red]budget_exceeded[/bold red]")
    console.print("[bold cyan]done[/bold cyan] • " + " • ".join(parts))


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


@app.command("replay")
def replay_cmd(
    dataset: Annotated[
        str,
        typer.Argument(help="Dataset name under tests/evals/datasets/."),
    ],
) -> None:
    """Replay a JSONL evals dataset through the synthesis pipeline.

    Each line is parsed into an :class:`InputContext`; ``run_query`` is invoked
    with the same dependency wiring as ``query``; the rendered :class:`Report`
    for each row goes to stdout. The dataset format itself ships with Task 11.
    """
    anyio.run(_replay, dataset)


@observe(name="cli.replay")
async def _replay(dataset: str) -> None:
    path = Path("tests/evals/datasets") / f"{dataset}.jsonl"
    if not path.exists():
        typer.echo(f"no dataset at {path}; ship Task 11", err=True)
        raise typer.Exit(code=2)

    config = load_config()
    _maybe_init_tracing(config)
    llm, embedder, corpus, budget = _build_deps(config)
    _set_corpus(corpus)

    progress_ctx: contextlib.AbstractContextManager[RichQueryProgress | None] = (
        RichQueryProgress() if sys.stderr.isatty() else contextlib.nullcontext()
    )
    with progress_ctx as bar:
        for raw_line in path.read_text().splitlines():
            line = raw_line.strip()
            if not line:
                continue
            # ``json.loads`` returns ``Any`` by design; the per-site ignore narrows
            # the unknown payload to ``object``. ``InputContext.model_validate`` is
            # the strict boundary.
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
                _render_query_footer(bar.console, report, n_target=config.N_synthesize)
            typer.echo(render(report))


# ---------------------------------------------------------------------------
# embed-prefetch
# ---------------------------------------------------------------------------


@app.command("embed-prefetch")
def embed_prefetch_cmd() -> None:
    """Warm the configured embedder's model cache (useful for CI / first-run)."""
    anyio.run(_embed_prefetch)


async def _embed_prefetch() -> None:
    config = load_config()
    budget = Budget(cap_usd=0.0)
    embedder = _make_embedder(config, budget)
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
