"""CLI: regenerate ``corpus_fixture.jsonl`` by running real ingest then dumping.

Operator-only entry point. Builds ``tests/fixtures/corpus_fixture.jsonl`` by:

1. Translating ``corpus_fixture_inputs.yml`` (``name``/``url``) into the curated
   YAML schema (``startup_name``/``url``) inside a ``TemporaryDirectory``.
2. Running the real ingest pipeline against a uniquely-named throwaway Qdrant
   collection. Per-pid + uuid4 collection naming makes any leak from ``kill -9``
   identifiable and droppable manually.
3. Scrolling the populated collection out via ``dump_collection_to_jsonl`` and
   atomically swapping a ``.recording`` temp file into place.

Gated on ``RUN_LIVE=1`` because every run hits OpenRouter and the embedder
(real spend). Production CLI surfaces (``slopmortem.cli``) stay untouched.
"""

import argparse
import asyncio
import contextlib
import os
import sys
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, cast

import yaml
from openai import AsyncOpenAI
from qdrant_client import AsyncQdrantClient

from slopmortem.budget import Budget
from slopmortem.config import load_config
from slopmortem.corpus.merge import MergeJournal
from slopmortem.corpus.qdrant_store import QdrantCorpus, ensure_collection
from slopmortem.corpus.sources.curated import CuratedSource
from slopmortem.ingest import BinocularsSlopClassifier, ingest
from slopmortem.llm.fastembed_client import FastEmbedEmbeddingClient
from slopmortem.llm.openai_embeddings import EMBED_DIMS, OpenAIEmbeddingClient
from slopmortem.llm.openrouter import OpenRouterClient

if TYPE_CHECKING:
    from slopmortem.corpus.sources.base import Enricher
    from slopmortem.ingest import Corpus as IngestCorpus
    from slopmortem.llm.embedding_client import EmbeddingClient

_DEFAULT_MAX_COST_USD = 1.5


def _translate_seed_yaml(src: Path, dst: Path) -> None:
    """Translate seed-input YAML (``name``/``url``) to curated schema (``startup_name``/``url``).

    The curated YAML schema is the one consumed by
    :class:`slopmortem.corpus.sources.curated.CuratedSource`. ``description`` is
    dropped because the curated path doesn't read it.

    Args:
        src: Path to the seed-input YAML — list of rows, each a mapping with
            string ``name`` and ``url``.
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
    """Run the real ingest against a throwaway collection and dump to JSONL.

    Wraps client + tempdir lifetimes in a single :class:`contextlib.AsyncExitStack`
    so every dependency closes on any exit path. The throwaway-collection drop
    stays in a plain ``try/finally`` because it's stateful, not a context
    manager.

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
        # ── Tempdir for journal sqlite + post_mortems_root + translated YAML ──
        tempdir = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="corpus_record_")))
        post_mortems_root = tempdir / "post_mortems"
        post_mortems_root.mkdir(parents=True, exist_ok=True)
        translated_yaml = tempdir / "curated.yml"
        _translate_seed_yaml(inputs_path, translated_yaml)

        # ── Journal (sqlite-backed; no async close, but init() builds schema) ──
        journal = MergeJournal(tempdir / "journal.sqlite")
        await journal.init()

        # ── Qdrant client; close() runs on any exit path ──
        qclient = AsyncQdrantClient(url=qdrant_url)
        stack.push_async_callback(qclient.close)

        # ── Wiring mirrors slopmortem.cli._build_ingest_deps prod path ──
        budget = Budget(cap_usd=max_cost_usd)
        openrouter_sdk = AsyncOpenAI(
            api_key=os.environ["OPENROUTER_API_KEY"],
            base_url=config.openrouter_base_url,
        )
        llm = OpenRouterClient(
            sdk=openrouter_sdk,
            budget=budget,
            model=config.model_facet,
        )

        embedder: EmbeddingClient
        if config.embedding_provider == "fastembed":
            embedder = FastEmbedEmbeddingClient(
                model=config.embed_model_id,
                budget=budget,
                cache_dir=config.embed_cache_dir,
            )
        elif config.embedding_provider == "openai":
            openai_sdk = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
            embedder = OpenAIEmbeddingClient(
                sdk=openai_sdk,
                budget=budget,
                model=config.embed_model_id,
            )
        else:
            valid = ("fastembed", "openai")
            msg = (
                f"unknown embedding_provider {config.embedding_provider!r}; valid choices: {valid}"
            )
            raise ValueError(msg)

        classifier = BinocularsSlopClassifier()

        # ── Throwaway collection: create now, drop in the finally below ──
        if config.embed_model_id not in EMBED_DIMS:
            msg = f"unknown embed model {config.embed_model_id!r}; add to EMBED_DIMS"
            raise ValueError(msg)
        dim = EMBED_DIMS[config.embed_model_id]
        await ensure_collection(qclient, collection_name, dim=dim)
        try:
            qcorpus = QdrantCorpus(
                client=qclient,
                collection=collection_name,
                post_mortems_root=post_mortems_root,
                facet_boost=config.facet_boost,
                rrf_k=config.rrf_k,
            )
            # ``QdrantCorpus`` ships ``upsert_chunk`` but not yet ``has_chunks``
            # / ``delete_chunks_for_canonical`` (see cli.py:432-436); cast at
            # the boundary so the strict ingest-side ``Corpus`` Protocol holds.
            corpus = cast("IngestCorpus", qcorpus)

            sources = [CuratedSource(yaml_path=translated_yaml)]
            enrichers: list[Enricher] = []

            _ = await ingest(
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
            )

            out_tmp = out_path.with_suffix(out_path.suffix + ".recording")
            out_tmp.parent.mkdir(parents=True, exist_ok=True)
            await dump_collection_to_jsonl(qclient, collection_name, out_tmp)
            # Plan-mandated atomic swap; ``os.replace`` is the documented surface.
            os.replace(out_tmp, out_path)  # noqa: PTH105 — plan calls for os.replace
            # Final stat() is the operator confirmation message; the recorder
            # is exiting after this line so a brief sync stat is acceptable.
            size_bytes = out_path.stat().st_size  # noqa: ASYNC240 — terminal sync stat
            print(f"wrote {out_path} ({size_bytes} bytes)")  # noqa: T201 — CLI surface
        finally:
            # Drop the throwaway collection on any exit (success or failure).
            # ``contextlib.suppress`` because the collection may not have been
            # created yet (e.g. ensure_collection raised) and a failed delete
            # must not mask the original exception.
            with contextlib.suppress(Exception):
                await qclient.delete_collection(collection_name)


def main(argv: list[str] | None = None) -> None:
    """CLI entry: parse args, gate on ``RUN_LIVE``, dispatch to :func:`_record`.

    Args:
        argv: Optional argv list for testing; ``None`` defaults to ``sys.argv``.

    Raises:
        SystemExit: Exit code 2 when ``RUN_LIVE`` is unset (live API spend
            guard). Exit code 0 on success.
    """
    parser = argparse.ArgumentParser(prog="slopmortem.evals.corpus_recorder")
    _ = parser.add_argument("--inputs", required=True, type=Path)
    _ = parser.add_argument("--out", required=True, type=Path)
    _ = parser.add_argument(
        "--max-cost-usd",
        type=float,
        default=_DEFAULT_MAX_COST_USD,
    )
    # Default mirrors ``slopmortem.cli._build_ingest_corpus``: QDRANT_HOST /
    # QDRANT_PORT env vars, falling back to localhost:6333. The Config object
    # does not carry a Qdrant URL, so env is the canonical source.
    default_qdrant_url = (
        f"http://{os.environ.get('QDRANT_HOST', 'localhost')}"
        f":{os.environ.get('QDRANT_PORT', '6333')}"
    )
    _ = parser.add_argument(
        "--qdrant-url",
        type=str,
        default=default_qdrant_url,
    )
    args = parser.parse_args(argv)

    if not os.environ.get("RUN_LIVE"):
        print(  # noqa: T201 — CLI surface
            "eval-record-corpus requires RUN_LIVE=1 (live API spend)",
            file=sys.stderr,
        )
        sys.exit(2)

    inputs_path = cast("Path", args.inputs)
    out_path = cast("Path", args.out)
    max_cost_usd = cast("float", args.max_cost_usd)
    qdrant_url = cast("str", args.qdrant_url)

    asyncio.run(
        _record(
            inputs_path=inputs_path,
            out_path=out_path,
            max_cost_usd=max_cost_usd,
            qdrant_url=qdrant_url,
        )
    )


if __name__ == "__main__":
    main()
