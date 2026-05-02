"""CLI: regenerate ``corpus_fixture.jsonl`` by running real ingest then dumping.

Operator-only, gated on ``RUN_LIVE=1`` (every run hits OpenRouter and the
embedder). Throwaway collection names embed pid+uuid4 so a ``kill -9`` leak
is identifiable and droppable by hand. Output lands via atomic
``.recording`` swap.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, cast

import anyio
import yaml
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from slopmortem.budget import Budget
from slopmortem.cli_progress import RichPhaseProgress
from slopmortem.config import load_config
from slopmortem.corpus import MergeJournal, QdrantCorpus, ensure_collection
from slopmortem.corpus.sources import CuratedSource
from slopmortem.ingest import INGEST_PHASE_LABELS, HaikuSlopClassifier, IngestPhase, ingest
from slopmortem.llm import EMBED_DIMS, OpenRouterClient, make_embedder

if TYPE_CHECKING:
    from slopmortem.corpus.sources import Enricher
    from slopmortem.ingest import Corpus as IngestCorpus
    from slopmortem.ingest import IngestResult

_DEFAULT_MAX_COST_USD = 1.5


class _RichIngestProgress(RichPhaseProgress[IngestPhase]):
    """Recorder-local Rich-backed ingest progress; mirrors cli.RichIngestProgress."""

    def __init__(self) -> None:
        super().__init__(INGEST_PHASE_LABELS)


def _make_progress_ctx() -> contextlib.AbstractContextManager[_RichIngestProgress | None]:
    """TTY-gated progress; piped invocations skip Live render so log capture stays readable."""
    return _RichIngestProgress() if sys.stderr.isatty() else contextlib.nullcontext()


def _render_recorder_summary(
    console: Console, *, out_path: Path, size_bytes: int, result: IngestResult
) -> None:
    """Recorder-side summary panel; built locally since the CLI's private impl is panel-coupled."""
    table = Table(show_header=False, expand=False, box=None)
    table.add_column("Field", style="bold cyan", no_wrap=True)
    table.add_column("Value", style="white")
    table.add_row("output", str(out_path))
    table.add_row("bytes", f"{size_bytes:,}")
    table.add_row("seen", str(result.seen))
    table.add_row("processed", str(result.processed))
    table.add_row("quarantined", str(result.quarantined))
    table.add_row("skipped", str(result.skipped))
    table.add_row("errors", str(result.errors))
    table.add_row("source_failures", str(result.source_failures))
    console.print(
        Panel(
            table,
            title="[bold cyan]corpus dump[/bold cyan]",
            title_align="left",
            border_style="cyan",
            expand=False,
        )
    )


def _translate_seed_yaml(src: Path, dst: Path) -> None:
    """Translate seed YAML (``name``/``url``) to curated schema (``startup_name``/``url``).

    Raises ``ValueError`` if not a list, or if a row lacks a string ``name`` or ``url``.
    """
    with src.open("r", encoding="utf-8") as fh:
        data: object = yaml.safe_load(fh) or []
    if not isinstance(data, list):
        msg = f"seed YAML at {src} must be a list of rows"
        raise ValueError(msg)  # noqa: TRY004 — plan contract is ValueError, not TypeError
    rows = cast("list[object]", data)
    out: list[dict[str, str]] = []
    for idx, row in enumerate(rows):
        if not isinstance(row, dict):
            msg = f"seed YAML row {idx} is not a mapping: {row!r}"
            raise ValueError(msg)  # noqa: TRY004 — plan contract is ValueError, not TypeError
        row_map = cast("dict[str, object]", row)
        name = row_map.get("name")
        url = row_map.get("url")
        if not isinstance(name, str) or not name:
            msg = f"seed YAML row {idx} missing string 'name': {row_map!r}"
            raise ValueError(msg)
        if not isinstance(url, str) or not url:
            msg = f"seed YAML row {idx} missing string 'url': {row_map!r}"
            raise ValueError(msg)
        out.append({"startup_name": name, "url": url})
    with dst.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(out, fh, sort_keys=False)


async def _record(
    inputs_path: Path,
    out_path: Path,
    max_cost_usd: float,
    qdrant_url: str,
) -> None:
    """Run the real ingest against a throwaway collection and dump it to JSONL.

    Client and tempdir lifetimes ride a single ``AsyncExitStack`` so they close
    on any exit path; the collection drop stays in a plain ``try/finally``
    since it's stateful, not a context manager.
    """
    # Lazy import: ingest helpers shouldn't load when callers only need
    # the YAML-translation surface.
    from slopmortem.evals.corpus_fixture import dump_collection_to_jsonl  # noqa: PLC0415

    config = load_config()
    collection_name = f"slopmortem_corpus_record_{os.getpid()}_{uuid.uuid4().hex}"

    async with contextlib.AsyncExitStack() as stack:
        tempdir = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="corpus_record_")))
        post_mortems_root = tempdir / "post_mortems"
        post_mortems_root.mkdir(parents=True, exist_ok=True)
        translated_yaml = tempdir / "curated.yml"
        _translate_seed_yaml(inputs_path, translated_yaml)

        # Fresh tempdir → journal schema doesn't exist; init() creates it.
        journal = MergeJournal(tempdir / "journal.sqlite")
        await journal.init()

        qclient = AsyncQdrantClient(url=qdrant_url)
        stack.push_async_callback(qclient.close)

        budget = Budget(cap_usd=max_cost_usd)
        openrouter_sdk = AsyncOpenAI(
            api_key=config.openrouter_api_key.get_secret_value(),
            base_url=config.openrouter_base_url,
        )
        llm = OpenRouterClient(
            sdk=openrouter_sdk,
            budget=budget,
            model=config.model_facet,
        )

        embedder = make_embedder(config, budget)

        classifier = HaikuSlopClassifier(llm=llm, model=config.model_summarize)

        if config.embed_model_id not in EMBED_DIMS:
            msg = f"unknown embed model {config.embed_model_id!r}; add to EMBED_DIMS"
            raise ValueError(msg)
        dim = EMBED_DIMS[config.embed_model_id]

        # ensure_collection inside the try so a partial-create still hits the
        # cleanup path; the suppress below covers the never-created case.
        try:
            await ensure_collection(qclient, collection_name, dim=dim)
            corpus: IngestCorpus = QdrantCorpus(
                client=qclient,
                collection=collection_name,
                post_mortems_root=post_mortems_root,
                facet_boost=config.facet_boost,
                rrf_k=config.rrf_k,
            )

            sources = [CuratedSource(yaml_path=translated_yaml)]
            enrichers: list[Enricher] = []

            with _make_progress_ctx() as bar:
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
                    progress=bar,
                )

            # Append (not replace): with_suffix(".recording") would clobber .jsonl.
            out_tmp = out_path.with_suffix(out_path.suffix + ".recording")
            out_tmp.parent.mkdir(parents=True, exist_ok=True)
            await dump_collection_to_jsonl(qclient, collection_name, out_tmp)
            out_tmp.replace(out_path)
            size_bytes = out_path.stat().st_size  # noqa: ASYNC240 — last line before exit
            summary_console = bar.console if bar is not None else Console(stderr=True)
            _render_recorder_summary(
                summary_console,
                out_path=out_path,
                size_bytes=size_bytes,
                result=result,
            )
        finally:
            # Collection may not exist (ensure_collection failed before
            # creating it); a delete failure must not mask the in-flight
            # exception.
            with contextlib.suppress(Exception):
                await qclient.delete_collection(collection_name)


def main(argv: list[str] | None = None) -> None:
    """Parse args, gate on ``RUN_LIVE`` (exit 2 if unset), dispatch to ``_record``."""
    parser = argparse.ArgumentParser(prog="slopmortem.evals.corpus_recorder")
    _ = parser.add_argument("--inputs", required=True, type=Path)
    _ = parser.add_argument("--out", required=True, type=Path)
    _ = parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=_DEFAULT_MAX_COST_USD,
    )
    _ = parser.add_argument(
        "--qdrant-url",
        type=str,
        default=None,
    )
    args = parser.parse_args(argv)

    if not os.environ.get("RUN_LIVE"):
        print(  # noqa: T201 — CLI surface
            "eval-record-corpus requires RUN_LIVE=1 (live API spend)",
            file=sys.stderr,
        )
        sys.exit(2)

    config = load_config()
    inputs_path = cast("Path", args.inputs)
    out_path = cast("Path", args.out)
    max_cost_usd = cast("float", args.max_cost_usd)
    qdrant_url = cast("str | None", args.qdrant_url) or (
        f"http://{config.qdrant_host}:{config.qdrant_port}"
    )

    async def _do_record() -> None:
        await _record(
            inputs_path=inputs_path,
            out_path=out_path,
            max_cost_usd=max_cost_usd,
            qdrant_url=qdrant_url,
        )

    anyio.run(_do_record)


if __name__ == "__main__":
    main()
