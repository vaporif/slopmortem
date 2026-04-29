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

import functools
import json
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, cast

import anyio
import typer
from lmnr import Laminar
from openai import AsyncOpenAI

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
from slopmortem.ingest import ingest
from slopmortem.llm.openai_embeddings import OpenAIEmbeddingClient
from slopmortem.llm.openrouter import OpenRouterClient
from slopmortem.models import InputContext
from slopmortem.pipeline import run_query
from slopmortem.render import render
from slopmortem.tracing import init_tracing

if TYPE_CHECKING:
    from collections.abc import Callable

    from slopmortem.config import Config
    from slopmortem.corpus.merge import MergeJournal
    from slopmortem.corpus.sources.base import Enricher, Source
    from slopmortem.corpus.store import Corpus
    from slopmortem.ingest import Corpus as IngestCorpus
    from slopmortem.ingest import SlopClassifier
    from slopmortem.llm.client import LLMClient
    from slopmortem.llm.embedding_client import EmbeddingClient

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
        )
    )


async def _run_ingest(  # noqa: PLR0913 — the ingest CLI surface is wide.
    *,
    dry_run: bool,
    force: bool,
    reconcile_flag: bool,
    reclassify: bool,
    list_review: bool,
    crunchbase_csv: Path | None,
    enrich_wayback: bool,
    tavily_enrich: bool,
    post_mortems_root: Path,
) -> None:
    """Async impl behind ``slopmortem ingest``. Resolves wiring then dispatches."""
    config = load_config()
    _maybe_init_tracing(config)
    llm, embedder, corpus, budget, journal, classifier = _build_ingest_deps(
        config, post_mortems_root
    )

    # Spec: §Quarantine and reclassify line 252.
    if reclassify:
        report = await reclassify_quarantined(
            journal=journal,
            slop_classifier=classifier,
            post_mortems_root=post_mortems_root,
            slop_threshold=config.slop_threshold,
        )
        counts = f"total={report.total} declassified={report.declassified}"
        tail = f"still_slop={report.still_slop} errors={report.errors}"
        typer.echo(f"reclassify: {counts} {tail}")
        return

    # Spec: §Atomicity / six drift classes (a-f).
    if reconcile_flag:
        report = await reconcile(
            journal=journal,
            corpus=corpus,
            post_mortems_root=post_mortems_root,
            repair=True,
        )
        typer.echo(f"reconcile: {len(report.rows)} drift findings, {len(report.applied)} repaired")
        for r in report.rows:
            typer.echo(f"  drift_class={r.drift_class}\t{r.path}\t{r.detail}")
        return

    if list_review:
        rows = await journal.list_pending_review()
        if not rows:
            typer.echo("(no pending_review rows)")
        for r in rows:
            score = r.similarity_score
            decision = r.haiku_decision
            rationale = r.haiku_rationale
            typer.echo(f"{r.pair_key}\tscore={score}\tdecision={decision}\trationale={rationale}")
        return

    # The ingest-side Corpus Protocol is wider than the query-side one used by
    # ``_set_corpus``; the underlying ``QdrantCorpus`` instance satisfies both.
    _set_corpus(cast("Corpus", corpus))

    sources: list[Source] = [
        CuratedSource(yaml_path=_default_curated_yaml()),
        HNAlgoliaSource(query="post-mortem"),
    ]
    if crunchbase_csv is not None:
        sources.append(CrunchbaseCsvSource(csv_path=crunchbase_csv))

    enrichers: list[Enricher] = []
    if enrich_wayback:
        enrichers.append(WaybackEnricher())
    if tavily_enrich:
        enrichers.append(TavilyEnricher())

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
    )
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
) -> None:
    """Run the synthesis pipeline against *description* and print a Markdown report.

    Streams stage progress to stderr (TTY-gated) while the pipeline runs; emits
    the rendered :class:`Report` to stdout when done. Tracing wiring (Laminar)
    is gated on ``Config.enable_tracing`` plus a present ``LMNR_PROJECT_API_KEY``.
    When the API key is missing but tracing is enabled, a one-line warning goes
    to stderr and the run continues without tracing.
    """
    anyio.run(functools.partial(_query, description=description, name=name, years=years))


async def _query(*, description: str, name: str | None, years: int | None) -> None:
    """Async impl for ``slopmortem query``. Wires production deps and dispatches."""
    config = load_config()
    _maybe_init_tracing(config)
    llm, embedder, corpus, budget = _build_deps(config)
    _set_corpus(corpus)
    ctx = InputContext(name=name or "(unnamed)", description=description, years_filter=years)
    report = await run_query(
        ctx,
        llm=llm,
        embedding_client=embedder,
        corpus=corpus,
        config=config,
        budget=budget,
        progress=_make_progress(),
    )
    typer.echo(render(report))


def _maybe_init_tracing(config: Config) -> None:
    """Run the endpoint guard, then conditionally call ``Laminar.initialize``.

    Always runs the SSRF-style endpoint guard (a no-op when ``LMNR_BASE_URL``
    is unset). If tracing is enabled in config but the project API key is
    missing, log to stderr and skip Laminar init. Tracing is best-effort.
    """
    base_url = os.environ.get("LMNR_BASE_URL")
    init_tracing(
        base_url=base_url,
        allow_remote=bool(os.environ.get("LMNR_ALLOW_REMOTE")),
    )
    if not config.enable_tracing:
        return
    api_key = os.environ.get("LMNR_PROJECT_API_KEY")
    if api_key is None or api_key == "":
        print(  # noqa: T201 — CLI surface; intentional stderr write
            "slopmortem: LMNR_PROJECT_API_KEY missing; tracing disabled",
            file=sys.stderr,
        )
        return
    Laminar.initialize(project_api_key=api_key, base_url=base_url)


def _build_deps(
    config: Config,
) -> tuple[LLMClient, EmbeddingClient, Corpus, Budget]:
    """Construct production LLM, embedder, corpus, and budget from *config* and env.

    Factored out as a helper so CLI smoke tests can monkeypatch a single symbol.
    Reads ``OPENROUTER_API_KEY`` / ``OPENAI_API_KEY`` from env; the Qdrant
    client honors ``QDRANT_HOST`` / ``QDRANT_PORT`` (falling back to
    ``localhost:6333``) and ``POST_MORTEMS_ROOT`` (falling back to
    ``./post_mortems``).
    """
    from qdrant_client import AsyncQdrantClient  # noqa: PLC0415 — heavy dep, lazy import

    budget = Budget(cap_usd=config.max_cost_usd_per_query)

    openrouter_sdk = AsyncOpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=config.openrouter_base_url,
    )
    llm = OpenRouterClient(
        sdk=openrouter_sdk,
        budget=budget,
        model=config.model_synthesize,
    )

    openai_sdk = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    embedder = OpenAIEmbeddingClient(
        sdk=openai_sdk,
        budget=budget,
        model=config.embed_model_id,
    )

    qdrant_host = os.environ.get("QDRANT_HOST", "localhost")
    qdrant_port = int(os.environ.get("QDRANT_PORT", "6333"))
    qdrant_client = AsyncQdrantClient(host=qdrant_host, port=qdrant_port)
    corpus = QdrantCorpus(
        client=qdrant_client,
        collection=os.environ.get("QDRANT_COLLECTION", "slopmortem"),
        post_mortems_root=Path(os.environ.get("POST_MORTEMS_ROOT", "./post_mortems")),
        facet_boost=config.facet_boost,
        rrf_k=config.rrf_k,
    )

    return llm, embedder, corpus, budget


def _build_ingest_deps(
    config: Config,
    post_mortems_root: Path,
) -> tuple[LLMClient, EmbeddingClient, IngestCorpus, Budget, MergeJournal, SlopClassifier]:
    """Construct the ingest-side wiring: LLM / embed / corpus / budget / journal / classifier.

    Mirrors :func:`_build_deps` but uses ``max_cost_usd_per_ingest`` for the
    budget cap and additionally constructs a :class:`MergeJournal` and the
    production :class:`BinocularsSlopClassifier`.
    """
    from qdrant_client import AsyncQdrantClient  # noqa: PLC0415 — heavy dep, lazy import

    from slopmortem.corpus.merge import MergeJournal  # noqa: PLC0415
    from slopmortem.ingest import BinocularsSlopClassifier  # noqa: PLC0415

    budget = Budget(cap_usd=config.max_cost_usd_per_ingest)

    openrouter_sdk = AsyncOpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=config.openrouter_base_url,
    )
    llm = OpenRouterClient(
        sdk=openrouter_sdk,
        budget=budget,
        model=config.model_facet,  # ingest uses the cheap model
    )

    openai_sdk = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    embedder = OpenAIEmbeddingClient(
        sdk=openai_sdk,
        budget=budget,
        model=config.embed_model_id,
    )

    qdrant_host = os.environ.get("QDRANT_HOST", "localhost")
    qdrant_port = int(os.environ.get("QDRANT_PORT", "6333"))
    qdrant_client = AsyncQdrantClient(host=qdrant_host, port=qdrant_port)
    corpus = QdrantCorpus(
        client=qdrant_client,
        collection=os.environ.get("QDRANT_COLLECTION", "slopmortem"),
        post_mortems_root=post_mortems_root,
        facet_boost=config.facet_boost,
        rrf_k=config.rrf_k,
    )

    journal_path = Path(
        os.environ.get("MERGE_JOURNAL_PATH", str(post_mortems_root.parent / "journal.sqlite"))
    )
    journal = MergeJournal(journal_path)

    classifier = BinocularsSlopClassifier()

    # ``QdrantCorpus`` ships ``upsert_chunk`` but not yet ``has_chunks`` /
    # ``delete_chunks_for_canonical`` (production gap tracked separately);
    # cast at this boundary so the CLI surface compiles against the strict
    # ingest-side ``Corpus`` Protocol declared in :mod:`slopmortem.ingest`.
    ingest_corpus = cast("IngestCorpus", corpus)
    return llm, embedder, ingest_corpus, budget, journal, classifier


def _make_progress() -> Callable[[str], None] | None:
    """Build a stderr progress callback, or ``None`` when stderr is not a TTY."""
    if not sys.stderr.isatty():
        return None

    def _p(msg: str) -> None:
        print(f"slopmortem: {msg}", file=sys.stderr, flush=True)  # noqa: T201 — CLI progress

    return _p


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


async def _replay(dataset: str) -> None:
    path = Path("tests/evals/datasets") / f"{dataset}.jsonl"
    if not path.exists():
        typer.echo(f"no dataset at {path}; ship Task 11", err=True)
        raise typer.Exit(code=2)

    config = load_config()
    _maybe_init_tracing(config)
    llm, embedder, corpus, budget = _build_deps(config)
    _set_corpus(corpus)

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
            progress=_make_progress(),
        )
        typer.echo(render(report))


if __name__ == "__main__":
    app()
