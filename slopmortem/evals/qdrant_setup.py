"""Async context manager for an ephemeral Qdrant collection: spin, populate from JSONL, drop."""

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

    Name embeds ``pid + uuid4`` so a ``kill -9`` leak is identifiable and
    droppable manually. No startup sweep — under pytest-xdist a prefix-wide
    sweep would drop a sibling worker's still-active collection.
    ``post_mortems_root`` only matters for ``get_post_mortem``, which the
    recording path doesn't exercise.
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
