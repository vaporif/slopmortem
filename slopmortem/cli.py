# ruff: noqa: FBT002 - typer signatures are bool flags with False defaults by convention.
"""Top-level CLI: ``slopmortem ingest``, ``query``, and ``replay``.

``query`` is the production entry point for the synthesis pipeline. Loads
:class:`Config`, starts Laminar tracing (gated on the endpoint guard in
:mod:`slopmortem.tracing` plus an env-var API key), builds the real OpenRouter
LLM client, the OpenAI embedding client, and the Qdrant corpus, then dispatches
to :func:`slopmortem.pipeline.run_query`. Stage progress goes to stderr
(TTY-gated); the rendered Markdown report goes to stdout.

``replay`` iterates an evals dataset (format + content shipped in Task 11). The
missing-dataset path exits with code 2 so CI smoke tests can probe the wiring
without a fixture corpus.

``ingest`` assembles real :class:`Source`, :class:`Enricher`,
:class:`MergeJournal`, :class:`Corpus`, :class:`LLMClient`,
:class:`EmbeddingClient`, and :class:`SlopClassifier` from :class:`Config` and
env vars, then dispatches to :func:`slopmortem.ingest.ingest`. Read-only modes:
``--list-review`` queries :class:`MergeJournal` for the pending entity-resolution
review queue. ``--reconcile`` runs the six-drift-class scan with ``repair=True``
and prints the report. ``--reclassify`` re-runs the slop classifier on every
quarantine row and routes survivors back out of the quarantine tree.
``--tavily-enrich`` appends a :class:`TavilyEnricher` to the enrichers list.
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
from typing import TYPE_CHECKING, Annotated, cast

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
from rich.table import Table

from slopmortem.budget import Budget
from slopmortem.cli_progress import RichPhaseProgress
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
from slopmortem.ingest import INGEST_PHASE_LABELS, IngestPhase, IngestResult, ingest
from slopmortem.llm.embedding_factory import make_embedder
from slopmortem.llm.fastembed_client import FastEmbedEmbeddingClient
from slopmortem.llm.openrouter import OpenRouterClient
from slopmortem.models import InputContext
from slopmortem.pipeline import QueryPhase, cutoff_iso, run_query
from slopmortem.render import render
from slopmortem.stages.facet_extract import extract_facets
from slopmortem.stages.retrieve import retrieve
from slopmortem.tracing import init_tracing

if TYPE_CHECKING:
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
def ingest_cmd(  # noqa: PLR0913 - every flag mirrors the spec; user types kwargs.
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
    reliability_rank_version, ...) come from
    :func:`slopmortem.config.load_config`.
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
    # reclassify hits the live slop judge, so we need the LLM but neither the
    # embedder nor qdrant.
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
async def _run_ingest(  # noqa: PLR0913, C901 - the ingest CLI surface is wide.
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

    # Read-only short-circuits run before the full LLM/embedder build so they
    # don't require ``OPENROUTER_API_KEY`` / ``OPENAI_API_KEY``.
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

    if reclassify:
        await _run_reclassify(config, post_mortems_root)
        return

    if reconcile_flag:
        await _run_reconcile(config, post_mortems_root)
        return

    llm, embedder, corpus, budget, journal, classifier = await _build_ingest_deps(
        config, post_mortems_root, dry_run=dry_run
    )
    # The ingest-side Corpus Protocol is wider than the query-side one used by
    # _set_corpus; the underlying QdrantCorpus instance satisfies both.
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

    # TTY-gated: attach Rich progress only when stderr is a real terminal.
    # Piped invocations (CI, ``> file``) get a quiet run.
    progress_ctx: contextlib.AbstractContextManager[RichIngestProgress | None] = (
        RichIngestProgress() if sys.stderr.isatty() else contextlib.nullcontext()
    )
    # Print escaping exceptions before Rich tears down — Progress.__exit__
    # clears the screen, so a traceback interleaved with bar redraws disappears.
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
        # BaseException, so without an explicit handler the prompt returns
        # silently with no sign the run was interrupted.
        err_console.rule("[bold yellow]ingest cancelled (Ctrl-C)", style="yellow")
        raise
    except BaseException:
        # Covers CancelledError / SystemExit / etc that ``except Exception``
        # misses. Re-raise so the caller still gets a non-zero exit.
        err_console.rule("[bold red]ingest failed", style="red")
        err_console.print_exception(show_locals=False)
        raise
    if bar is not None:
        _render_ingest_result(bar.console, result, budget)
    else:
        typer.echo(f"slopmortem ingest result: {result} cost=${budget.spent_usd:.4f}")


def _default_curated_yaml() -> Path:
    """Return the in-tree curated post-mortem YAML path shipped with the package."""
    return Path(__file__).parent / "corpus" / "sources" / "curated" / "post_mortems_v0.yml"


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
        print(  # noqa: T201 - CLI surface; intentional stderr write
            "slopmortem: LMNR_PROJECT_API_KEY missing; tracing disabled",
            file=sys.stderr,
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


async def _build_journal(config: Config, post_mortems_root: Path) -> MergeJournal:
    """Construct and schema-initialize the merge journal.

    ``init()`` is idempotent (``CREATE TABLE IF NOT EXISTS``), so calling it
    every CLI invocation is cheap and means fresh dev databases work without
    an explicit setup step.
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

    Dry-run uses :class:`FakeSlopClassifier` — no API key, no LLM cost. Live
    ingest uses :class:`HaikuSlopClassifier`: one Haiku call per entry,
    quarantines anything that isn't a specific dead-company narrative.
    """
    if dry_run:
        from slopmortem.ingest import FakeSlopClassifier  # noqa: PLC0415

        return FakeSlopClassifier()
    from slopmortem.ingest import HaikuSlopClassifier  # noqa: PLC0415

    return HaikuSlopClassifier(llm=llm, model=model, max_tokens=max_tokens)


async def _build_ingest_corpus(config: Config, post_mortems_root: Path) -> IngestCorpus:
    """Construct the Qdrant-backed ingest corpus and ensure its collection exists.

    ``ensure_collection`` is idempotent (creates only when missing), so this is
    cheap to call every invocation and means fresh dev boxes don't need a
    separate setup step. Without it, ``upsert_chunk`` fails with
    ``Collection 'slopmortem' doesn't exist`` on the first write.
    """
    from qdrant_client import AsyncQdrantClient  # noqa: PLC0415 - heavy dep, lazy import

    from slopmortem.corpus.qdrant_store import ensure_collection  # noqa: PLC0415
    from slopmortem.llm.openai_embeddings import EMBED_DIMS  # noqa: PLC0415

    if config.embed_model_id not in EMBED_DIMS:
        msg = f"unknown embed model {config.embed_model_id!r}; add it to EMBED_DIMS"
        raise ValueError(msg)
    dim = EMBED_DIMS[config.embed_model_id]

    qdrant_client = AsyncQdrantClient(host=config.qdrant_host, port=config.qdrant_port)
    await ensure_collection(qdrant_client, config.qdrant_collection, dim=dim)
    return QdrantCorpus(
        client=qdrant_client,
        collection=config.qdrant_collection,
        post_mortems_root=post_mortems_root,
        facet_boost=config.facet_boost,
        rrf_k=config.rrf_k,
    )


async def _build_ingest_deps(
    config: Config,
    post_mortems_root: Path,
    *,
    dry_run: bool,
) -> tuple[LLMClient, EmbeddingClient, IngestCorpus, Budget, MergeJournal, SlopClassifier]:
    """Construct the full ingest-side wiring: LLM, embed, corpus, budget, journal, classifier.

    Mirrors :func:`_build_deps` but caps the budget at
    ``max_cost_usd_per_ingest`` and additionally builds a
    :class:`MergeJournal` plus the slop classifier. ``dry_run=True`` swaps in
    :class:`FakeSlopClassifier` so the run needs no real API key.
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

    embedder = make_embedder(config, budget)

    corpus = await _build_ingest_corpus(config, post_mortems_root)
    journal = await _build_journal(config, post_mortems_root)
    classifier = _build_slop_classifier(
        dry_run=dry_run,
        llm=llm,
        model=config.model_summarize,
        max_tokens=config.max_tokens_slop_judge,
    )
    return llm, embedder, corpus, budget, journal, classifier


class RichIngestProgress(RichPhaseProgress[IngestPhase]):
    """Rich-backed :class:`slopmortem.ingest.IngestProgress` impl."""

    def __init__(self) -> None:
        """Build with ingest phase labels."""
        super().__init__(INGEST_PHASE_LABELS)


def _render_ingest_result(console: Console, result: IngestResult, budget: Budget) -> None:
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
        ("cost_usd", f"${budget.spent_usd:.4f}"),
    ]
    table = Table(title="Ingest result", show_header=False, expand=False)
    table.add_column("Field", style="bold cyan", no_wrap=True)
    table.add_column("Value", style="white")
    for k, v in rows:
        table.add_row(k, v)
    console.print(table)


_QUERY_PHASE_LABELS: dict[QueryPhase, str] = {
    QueryPhase.FACET_EXTRACT: "Extracting facets",
    QueryPhase.RETRIEVE: "Retrieving candidates",
    QueryPhase.RERANK: "Reranking candidates",
    QueryPhase.SYNTHESIZE: "Synthesizing post-mortems",
}


class RichQueryProgress(RichPhaseProgress[QueryPhase]):
    """Rich-backed :class:`slopmortem.pipeline.QueryProgress` impl."""

    def __init__(self) -> None:
        """Build with query phase labels."""
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
    row goes to stdout. Dataset format ships with Task 11.
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
