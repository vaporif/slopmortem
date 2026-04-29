from __future__ import annotations

import pytest
from qdrant_client.models import Modifier

from slopmortem.corpus.qdrant_store import ensure_collection
from slopmortem.llm.openai_embeddings import EMBED_DIMS


@pytest.mark.requires_qdrant
async def test_collection_has_idf_modifier(qdrant_client):
    name = "test_collection_idf"
    if await qdrant_client.collection_exists(name):
        await qdrant_client.delete_collection(name)
    await ensure_collection(qdrant_client, name, dim=EMBED_DIMS["text-embedding-3-small"])
    info = await qdrant_client.get_collection(name)
    sparse = info.config.params.sparse_vectors["sparse"]
    assert sparse.modifier == Modifier.IDF
    await qdrant_client.delete_collection(name)


@pytest.mark.requires_qdrant
async def test_collection_dim_mismatch_raises(qdrant_client):
    name = "test_dim_mismatch"
    if await qdrant_client.collection_exists(name):
        await qdrant_client.delete_collection(name)
    await ensure_collection(qdrant_client, name, dim=EMBED_DIMS["text-embedding-3-small"])
    with pytest.raises(ValueError, match="dim mismatch"):
        await ensure_collection(qdrant_client, name, dim=EMBED_DIMS["text-embedding-3-large"])
    await qdrant_client.delete_collection(name)


@pytest.mark.requires_qdrant
async def test_ensure_collection_idempotent(qdrant_client):
    name = "test_collection_idempotent"
    if await qdrant_client.collection_exists(name):
        await qdrant_client.delete_collection(name)
    dim = EMBED_DIMS["text-embedding-3-small"]
    await ensure_collection(qdrant_client, name, dim=dim)
    await ensure_collection(qdrant_client, name, dim=dim)
    info = await qdrant_client.get_collection(name)
    assert info.config.params.vectors["dense"].size == dim
    await qdrant_client.delete_collection(name)
