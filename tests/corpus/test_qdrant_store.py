"""Live-Qdrant tests for ``QdrantCorpus.delete_chunks_for_canonical``."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from slopmortem.corpus import QdrantCorpus, ensure_collection
from slopmortem.ingest import _Point
from slopmortem.llm import EMBED_DIMS

if TYPE_CHECKING:
    from pathlib import Path

    from qdrant_client import AsyncQdrantClient

_DIM = EMBED_DIMS["text-embedding-3-small"]


def _make_chunk(canonical_id: str, idx: int) -> _Point:
    # Distinct dense vectors per chunk so Qdrant indexes them as separate
    # points; the sparse half is required by the hybrid collection schema.
    dense = [float((idx + 1) * 0.001)] * _DIM
    sparse: dict[int, float] = {idx: 1.0}
    return _Point(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"{canonical_id}:{idx}").hex,
        vector={"dense": dense, "sparse": sparse},
        payload={"canonical_id": canonical_id, "chunk_idx": idx},
    )


async def _build_corpus(
    qdrant_client: AsyncQdrantClient,
    tmp_path: Path,
    name: str,
) -> QdrantCorpus:
    if await qdrant_client.collection_exists(name):
        await qdrant_client.delete_collection(name)
    await ensure_collection(qdrant_client, name, dim=_DIM)
    return QdrantCorpus(
        client=qdrant_client,
        collection=name,
        post_mortems_root=tmp_path,
    )


@pytest.mark.requires_qdrant
async def test_delete_chunks_for_canonical_removes_matching_points(
    qdrant_client: AsyncQdrantClient, tmp_path: Path
) -> None:
    name = "test_delete_chunks_match"
    corpus = await _build_corpus(qdrant_client, tmp_path, name)
    try:
        canonical_id = "test:abc123"
        other = "test:other"
        for idx in range(3):
            await corpus.upsert_chunk(_make_chunk(canonical_id, idx))
        await corpus.upsert_chunk(_make_chunk(other, 0))

        await corpus.delete_chunks_for_canonical(canonical_id)

        # No `get_chunks` accessor exists; verify via scroll + filter.
        from qdrant_client.http.models import (  # noqa: PLC0415
            FieldCondition,
            Filter,
            MatchValue,
        )

        matched, _ = await qdrant_client.scroll(
            collection_name=name,
            scroll_filter=Filter(
                must=[FieldCondition(key="canonical_id", match=MatchValue(value=canonical_id))]
            ),
            limit=10,
        )
        assert matched == []
        other_matched, _ = await qdrant_client.scroll(
            collection_name=name,
            scroll_filter=Filter(
                must=[FieldCondition(key="canonical_id", match=MatchValue(value=other))]
            ),
            limit=10,
        )
        assert len(other_matched) == 1
    finally:
        await qdrant_client.delete_collection(name)


@pytest.mark.requires_qdrant
async def test_delete_chunks_idempotent_when_no_points(
    qdrant_client: AsyncQdrantClient, tmp_path: Path
) -> None:
    name = "test_delete_chunks_idempotent"
    corpus = await _build_corpus(qdrant_client, tmp_path, name)
    try:
        # Must not raise even though no points exist for this canonical_id.
        await corpus.delete_chunks_for_canonical("nonexistent:id")
    finally:
        await qdrant_client.delete_collection(name)
