from __future__ import annotations

import pytest

from slopmortem.budget import Budget
from slopmortem.config import Config
from slopmortem.llm.embedding_factory import make_embedder
from slopmortem.llm.fastembed_client import FastEmbedEmbeddingClient
from slopmortem.llm.openai_embeddings import OpenAIEmbeddingClient


def test_factory_returns_fastembed_for_fastembed_provider() -> None:
    cfg = Config(embedding_provider="fastembed", embed_model_id="nomic-ai/nomic-embed-text-v1.5")
    e = make_embedder(cfg, Budget(0.0))
    assert isinstance(e, FastEmbedEmbeddingClient)
    assert e.model == "nomic-ai/nomic-embed-text-v1.5"


def test_factory_returns_openai_for_openai_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = Config(embedding_provider="openai", embed_model_id="text-embedding-3-small")
    e = make_embedder(cfg, Budget(0.0))
    assert isinstance(e, OpenAIEmbeddingClient)
    assert e.model == "text-embedding-3-small"


def test_factory_raises_on_unknown_provider() -> None:
    cfg = Config(embedding_provider="ollama", embed_model_id="text-embedding-3-small")
    with pytest.raises(ValueError, match="ollama"):
        make_embedder(cfg, Budget(0.0))
