from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from slopmortem.budget import Budget
from slopmortem.config import Config
from slopmortem.llm import (
    EMBED_DIMS,
    OPENAI_EMBED_MODELS,
    EmbeddingClient,
    FakeEmbeddingClient,
    OpenAIEmbeddingClient,
)

_PRICES_PATH = Path(__file__).resolve().parents[2] / "slopmortem" / "llm" / "prices.yml"


def _stub_embed_response(*, dim: int, n: int = 1, total_tokens: int = 10):
    """Mirror the openai SDK shape: resp.data[i].embedding, resp.usage.total_tokens."""
    return SimpleNamespace(
        data=[SimpleNamespace(embedding=[0.0] * dim) for _ in range(n)],
        usage=SimpleNamespace(total_tokens=total_tokens),
    )


@pytest.fixture
def fake_sdk():
    sdk = MagicMock()
    sdk.embeddings = MagicMock()
    sdk.embeddings.create = AsyncMock()
    return sdk


def test_unknown_model_raises_with_embed_dims_in_message(fake_sdk):
    with pytest.raises(ValueError, match="does not support model"):
        OpenAIEmbeddingClient(sdk=fake_sdk, budget=Budget(0.01), model="text-embedding-3-xxl")


def test_dim_property_matches_embed_dims_for_known_models(fake_sdk):
    for model in OPENAI_EMBED_MODELS:
        c = OpenAIEmbeddingClient(sdk=fake_sdk, budget=Budget(0.01), model=model)
        assert c.dim == EMBED_DIMS[model]


async def test_embed_returns_vectors_matching_dim(fake_sdk):
    model = "text-embedding-3-small"
    dim = EMBED_DIMS[model]
    fake_sdk.embeddings.create.return_value = _stub_embed_response(dim=dim, n=3)
    c = OpenAIEmbeddingClient(sdk=fake_sdk, budget=Budget(1.0), model=model)
    r = await c.embed(["a", "b", "c"])
    assert len(r.vectors) == 3
    assert all(len(v) == dim for v in r.vectors)


async def test_cost_derived_from_prices_yml(fake_sdk, tmp_path):
    prices_path = _PRICES_PATH
    prices = yaml.safe_load(prices_path.read_text())
    rate = prices["openai/text-embedding-3-small"]["input"]

    model = "text-embedding-3-small"
    dim = EMBED_DIMS[model]
    fake_sdk.embeddings.create.return_value = _stub_embed_response(
        dim=dim, n=1, total_tokens=1_000_000
    )
    c = OpenAIEmbeddingClient(sdk=fake_sdk, budget=Budget(10.0), model=model)
    r = await c.embed(["x"])
    assert r.cost_usd == pytest.approx(rate)


async def test_budget_reserve_and_settle_called(fake_sdk):
    model = "text-embedding-3-small"
    dim = EMBED_DIMS[model]
    fake_sdk.embeddings.create.return_value = _stub_embed_response(dim=dim, n=1, total_tokens=1000)
    budget = Budget(1.0)
    reserved: list[float] = []
    settled: list[tuple[str, float]] = []

    real_reserve = budget.reserve
    real_settle = budget.settle

    async def spy_reserve(amount):
        reserved.append(amount)
        return await real_reserve(amount)

    async def spy_settle(rid, actual):
        settled.append((rid, actual))
        return await real_settle(rid, actual)

    budget.reserve = spy_reserve  # type: ignore[method-assign]
    budget.settle = spy_settle  # type: ignore[method-assign]

    c = OpenAIEmbeddingClient(sdk=fake_sdk, budget=budget, model=model)
    r = await c.embed(["x"])
    assert reserved, "expected reserve to be called"
    assert settled, "expected settle to be called"
    settled_amount = settled[-1][1]
    assert settled_amount == pytest.approx(r.cost_usd)


async def test_transient_failure_retries(fake_sdk):
    model = "text-embedding-3-small"
    dim = EMBED_DIMS[model]

    class _TransientError(Exception):
        status_code = 429

    fake_sdk.embeddings.create.side_effect = [
        _TransientError("rate limited"),
        _stub_embed_response(dim=dim, n=1),
    ]
    c = OpenAIEmbeddingClient(
        sdk=fake_sdk,
        budget=Budget(1.0),
        model=model,
        initial_backoff=0.0,
    )
    r = await c.embed(["x"])
    assert len(r.vectors) == 1
    assert fake_sdk.embeddings.create.call_count == 2


async def test_auth_error_is_fatal_no_retry(fake_sdk):
    model = "text-embedding-3-small"

    class _AuthError(Exception):
        status_code = 401

    fake_sdk.embeddings.create.side_effect = _AuthError("unauthorized")
    c = OpenAIEmbeddingClient(
        sdk=fake_sdk,
        budget=Budget(1.0),
        model=model,
        initial_backoff=0.0,
    )
    with pytest.raises(_AuthError):
        await c.embed(["x"])
    assert fake_sdk.embeddings.create.call_count == 1


async def test_fake_is_deterministic():
    a = FakeEmbeddingClient(model="text-embedding-3-small")
    b = FakeEmbeddingClient(model="text-embedding-3-small")
    ra = await a.embed(["hello world"])
    rb = await b.embed(["hello world"])
    assert ra.vectors == rb.vectors


async def test_fake_vector_length_matches_dim():
    model = "text-embedding-3-small"
    fake = FakeEmbeddingClient(model=model)
    r = await fake.embed(["hello", "world"])
    assert len(r.vectors) == 2
    assert all(len(v) == EMBED_DIMS[model] for v in r.vectors)


def test_fake_satisfies_embedding_protocol():
    fake = FakeEmbeddingClient(model="text-embedding-3-small")
    assert isinstance(fake, EmbeddingClient)


def test_fake_unknown_model_raises_with_embed_dims_in_message():
    with pytest.raises(ValueError, match="EMBED_DIMS"):
        FakeEmbeddingClient(model="text-embedding-3-xxl")


async def test_openai_embed_empty_input_returns_empty_without_calling_sdk(fake_sdk):
    c = OpenAIEmbeddingClient(sdk=fake_sdk, budget=Budget(1.0), model="text-embedding-3-small")
    r = await c.embed([])
    assert r.vectors == []
    assert r.n_tokens == 0
    assert r.cost_usd == 0.0
    fake_sdk.embeddings.create.assert_not_called()


def test_config_defaults_to_fastembed_with_nomic(tmp_path, monkeypatch):
    # No slopmortem.toml and no env, so we read pure code defaults.
    monkeypatch.chdir(tmp_path)
    cfg = Config()
    assert cfg.embedding_provider == "fastembed"
    assert cfg.embed_model_id == "nomic-ai/nomic-embed-text-v1.5"
    assert cfg.embed_cache_dir is None
