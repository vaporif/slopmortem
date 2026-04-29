"""LLM and embedding clients, prompt rendering, and OpenRouter retry logic."""

from slopmortem.llm.fake_embeddings import FakeEmbeddingClient
from slopmortem.llm.fastembed_client import FastEmbedEmbeddingClient
from slopmortem.llm.openai_embeddings import EMBED_DIMS, OpenAIEmbeddingClient

__all__ = [
    "EMBED_DIMS",
    "FakeEmbeddingClient",
    "FastEmbedEmbeddingClient",
    "OpenAIEmbeddingClient",
]
