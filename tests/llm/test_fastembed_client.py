from __future__ import annotations

import math
from typing import TYPE_CHECKING

import anyio
import pytest

from slopmortem.budget import Budget
from slopmortem.llm.fastembed_client import FastEmbedEmbeddingClient

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def test_dim_matches_embed_dims_registry():
    c = FastEmbedEmbeddingClient(model="nomic-ai/nomic-embed-text-v1.5", budget=Budget(0.0))
    assert c.dim == 768


async def test_embed_empty_returns_empty_without_loading_model():
    c = FastEmbedEmbeddingClient(model="nomic-ai/nomic-embed-text-v1.5", budget=Budget(0.0))
    r = await c.embed([])
    assert r.vectors == []
    assert r.n_tokens == 0
    assert r.cost_usd == 0.0
    # Model must not have been materialized.
    assert c._te is None


def test_unknown_model_raises_with_embed_dims_in_message():
    with pytest.raises(ValueError, match="EMBED_DIMS"):
        FastEmbedEmbeddingClient(model="nomic-embed-text-v999", budget=Budget(0.0))


async def test_per_call_model_override_rejected():
    c = FastEmbedEmbeddingClient(model="nomic-ai/nomic-embed-text-v1.5", budget=Budget(0.0))
    with pytest.raises(ValueError, match="not supported"):
        await c.embed(["x"], model="text-embedding-3-small")


async def test_concurrent_ensure_loaded_loads_model_only_once():
    """Two parallel callers must share a single _load_sync invocation."""
    c = FastEmbedEmbeddingClient(model="nomic-ai/nomic-embed-text-v1.5", budget=Budget(0.0))
    call_count = 0
    sentinel = object()

    def fake_load_sync() -> object:
        nonlocal call_count
        call_count += 1
        return sentinel

    c._load_sync = fake_load_sync  # pyright: ignore[reportAttributeAccessIssue]

    async with anyio.create_task_group() as tg:
        tg.start_soon(c._ensure_loaded)
        tg.start_soon(c._ensure_loaded)

    assert call_count == 1
    assert c._te is sentinel


async def test_load_sync_failure_is_wrapped_with_prefetch_hint(monkeypatch: MonkeyPatch):
    """A fastembed load failure surfaces as RuntimeError pointing at embed-prefetch."""
    import fastembed

    def boom(**_kwargs: object) -> object:
        msg = "corrupted model file"
        raise OSError(msg)

    monkeypatch.setattr(fastembed, "TextEmbedding", boom)
    c = FastEmbedEmbeddingClient(model="nomic-ai/nomic-embed-text-v1.5", budget=Budget(0.0))
    with pytest.raises(RuntimeError, match="embed-prefetch"):
        await c.prefetch()


@pytest.mark.slow
async def test_embed_returns_normalized_vectors_with_correct_dim(tmp_path):
    c = FastEmbedEmbeddingClient(
        model="nomic-ai/nomic-embed-text-v1.5",
        budget=Budget(0.0),
        cache_dir=tmp_path,
    )
    r = await c.embed(["hello", "world"])
    assert len(r.vectors) == 2
    assert all(len(v) == 768 for v in r.vectors)
    # Vectors must be L2-normalized so cosine == dot in Qdrant.
    for v in r.vectors:
        norm = math.sqrt(sum(x * x for x in v))
        assert norm == pytest.approx(1.0, rel=1e-3)
    assert r.cost_usd == 0.0
    assert r.n_tokens > 0
