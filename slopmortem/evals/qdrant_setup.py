"""Async context manager for an ephemeral Qdrant collection.

Spins a uniquely-named collection, populates it from a JSONL fixture, and
drops it on exit.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from qdrant_client import AsyncQdrantClient

from slopmortem.corpus import QdrantCorpus, ensure_collection
from slopmortem.evals.corpus_fixture import restore_jsonl_to_collection

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@asynccontextmanager
async def setup_ephemeral_qdrant(
    fixture_path: Path,
    *,
    qdrant_url: str = "http://localhost:6333",
    collection_prefix: str = "slopmortem_eval_",
    post_mortems_root: Path | None = None,
    dim: int = 768,
) -> AsyncGenerator[QdrantCorpus]:
    """Spin a uniquely-named collection, populate from JSONL, drop on exit.

    Collection name embeds ``pid + uuid4`` so a leak from ``kill -9`` is
    identifiable and droppable manually. No startup sweep — a prefix-wide
    sweep is unsafe under pytest-xdist (a sibling worker's still-active
    collection would get dropped).

    Args:
        fixture_path: JSONL file produced by
            :func:`slopmortem.evals.corpus_fixture.dump_collection_to_jsonl`.
        qdrant_url: Base URL of a live Qdrant service.
        collection_prefix: Prefix for the ephemeral collection name.
        post_mortems_root: Optional on-disk root for the canonical markdown
            tree. Defaults to ``/tmp/slopmortem_eval`` when ``None``. Only
            consulted by ``QdrantCorpus.get_post_mortem``, which the recording
            path doesn't exercise.
        dim: Dense vector dimensionality. Defaults to 768
            (``nomic-ai/nomic-embed-text-v1.5``); callers using a different
            embedder must pass the matching dim.

    Yields:
        A :class:`QdrantCorpus` bound to the freshly-populated collection.
    """
    name = f"{collection_prefix}{os.getpid()}_{uuid.uuid4().hex}"
    client = AsyncQdrantClient(url=qdrant_url)
    try:
        await ensure_collection(client, name, dim=dim)
        await restore_jsonl_to_collection(client, name, fixture_path)
        corpus = QdrantCorpus(
            client=client,
            collection=name,
            post_mortems_root=post_mortems_root or Path("/tmp/slopmortem_eval"),  # noqa: S108
        )
        yield corpus
    finally:
        with contextlib.suppress(Exception):
            await client.delete_collection(name)
        await client.close()
