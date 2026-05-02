"""CLI: regenerate ``corpus_fixture.jsonl`` by running real ingest then dumping.

Operator-only. Builds ``tests/fixtures/corpus_fixture.jsonl`` by:

1. Translating ``corpus_fixture_inputs.yml`` (``name``/``url``) into the curated
   YAML schema (``startup_name``/``url``) inside a ``TemporaryDirectory``.
2. Running the real ingest pipeline against a throwaway Qdrant collection. The
   pid + uuid4 in the collection name makes any leak (e.g. from ``kill -9``)
   easy to find and drop by hand.
3. Scrolling the collection out via ``dump_collection_to_jsonl`` and atomically
   swapping a ``.recording`` temp file into place.

Gated on ``RUN_LIVE=1``. Every run hits OpenRouter and the embedder, so the
gate exists to prevent accidental spend.
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
from slopmortem.corpus.merge import MergeJournal
from slopmortem.corpus.qdrant_store import QdrantCorpus, ensure_collection
from slopmortem.corpus.sources.curated import CuratedSource
from slopmortem.ingest import INGEST_PHASE_LABELS, HaikuSlopClassifier, IngestPhase, ingest
from slopmortem.llm.embedding_factory import make_embedder
from slopmortem.llm.openai_embeddings import EMBED_DIMS
from slopmortem.llm.openrouter import OpenRouterClient

if TYPE_CHECKING:
    from slopmortem.corpus.sources.base import Enricher
    from slopmortem.ingest import Corpus as IngestCorpus
    from slopmortem.ingest import IngestResult

_DEFAULT_MAX_COST_USD = 1.5


class _RichIngestProgress(RichPhaseProgress[IngestPhase]):
    """Recorder-local Rich-backed ingest progress; mirrors cli.RichIngestProgress."""

    def __init__(self) -> None:
        """Build with ingest phase labels."""
        super().__init__(INGEST_PHASE_LABELS)


def _make_progress_ctx() -> contextlib.AbstractContextManager[_RichIngestProgress | None]:
    """Return the TTY-gated progress context manager used by :func:`_record`.

    Piped invocations (CI, redirect-to-file) skip the Live render so log
    capture stays readable. Extracted into a helper so the gate can be tested
    without driving the heavy ``_record`` body end-to-end.
    """
    return _RichIngestProgress() if sys.stderr.isatty() else contextlib.nullcontext()


def _render_recorder_summary(
    console: Console, *, out_path: Path, size_bytes: int, result: IngestResult
) -> None:
    """Render a panel summarizing the corpus dump and the underlying ingest run.

    Mirrors the shape of ``slopmortem.cli._render_ingest_result`` plus the
    out-path / byte-size pair that's specific to the corpus recorder. Built
    locally rather than imported because the recorder needs the dump-side
    fields, and ``_render_ingest_result`` is private + tied to its own panel
    title.
    """
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

    The curated path doesn't read ``description``, so it's dropped.

    Args:
        src: Seed-input YAML, a list of rows, each a mapping with string
            ``name`` and ``url``.
        dst: Destination path for the translated curated YAML.

    Raises:
        ValueError: If the file is not a list, or the first bad row lacks a
            string ``name`` or ``url``.
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

    Client and tempdir lifetimes ride on a single :class:`contextlib.AsyncExitStack`
    so they close on any exit path. The collection drop stays in a plain
    ``try/finally`` since it's stateful, not a context manager.

    Args:
        inputs_path: Seed-input YAML; see :func:`_translate_seed_yaml`.
        out_path: Destination path for the canonical fixture JSONL.
        max_cost_usd: Hard cap on USD spend across LLM + embedding calls.
        qdrant_url: Base URL of a live Qdrant service (``http://host:port``).
    """
    # Lazy import to avoid a hard dependency on the ingest helpers when only
    # the YAML-translation surface is used.
    from slopmortem.evals.corpus_fixture import dump_collection_to_jsonl  # noqa: PLC0415

    config = load_config()
    collection_name = f"slopmortem_corpus_record_{os.getpid()}_{uuid.uuid4().hex}"

    async with contextlib.AsyncExitStack() as stack:
        tempdir = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="corpus_record_")))
        post_mortems_root = tempdir / "post_mortems"
        post_mortems_root.mkdir(parents=True, exist_ok=True)
        translated_yaml = tempdir / "curated.yml"
        _translate_seed_yaml(inputs_path, translated_yaml)

        # Fresh tempdir means the journal schema doesn't exist yet; init() creates it.
        journal = MergeJournal(tempdir / "journal.sqlite")
        await journal.init()

        qclient = AsyncQdrantClient(url=qdrant_url)
        stack.push_async_callback(qclient.close)

        # Wiring mirrors slopmortem.cli._build_ingest_deps.
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

        # ensure_collection lives inside the try so a partial-create still hits
        # the cleanup path. The suppress block below covers the never-created case.
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

            # TTY-gated mirror of slopmortem.cli's pattern: a piped invocation
            # (CI, redirect-to-file) skips the Live render so log capture stays
            # readable. Extracted gate lives in ``_make_progress_ctx`` so it can
            # be tested without driving the rest of ``_record``.
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

            # Append, not replace: with_suffix(".recording") would clobber .jsonl.
            out_tmp = out_path.with_suffix(out_path.suffix + ".recording")
            out_tmp.parent.mkdir(parents=True, exist_ok=True)
            await dump_collection_to_jsonl(qclient, collection_name, out_tmp)
            os.replace(out_tmp, out_path)  # noqa: PTH105 — atomic POSIX rename
            size_bytes = out_path.stat().st_size  # noqa: ASYNC240 — last line before exit
            summary_console = bar.console if bar is not None else Console(stderr=True)
            _render_recorder_summary(
                summary_console,
                out_path=out_path,
                size_bytes=size_bytes,
                result=result,
            )
        finally:
            # Suppress: the collection may not exist (ensure_collection failed
            # before creating it), and a delete failure must not mask whatever
            # exception is already propagating.
            with contextlib.suppress(Exception):
                await qclient.delete_collection(collection_name)


def main(argv: list[str] | None = None) -> None:
    """Parse args, gate on ``RUN_LIVE``, dispatch to :func:`_record`.

    Args:
        argv: Optional argv list for testing; ``None`` reads ``sys.argv``.

    Raises:
        SystemExit: Exit code 2 when ``RUN_LIVE`` is unset (spend guard);
            exit code 0 on success.
    """
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
