"""Provider-dispatch factory for the configured :class:`EmbeddingClient`.

Lives outside ``cli.py`` because the eval recorder builds the same wiring and
should not pull in CLI-only deps via a private import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openai import AsyncOpenAI

from slopmortem.llm.fastembed_client import FastEmbedEmbeddingClient
from slopmortem.llm.openai_embeddings import OpenAIEmbeddingClient

if TYPE_CHECKING:
    from slopmortem.budget import Budget
    from slopmortem.config import Config
    from slopmortem.llm.embedding_client import EmbeddingClient


def make_embedder(config: Config, budget: Budget) -> EmbeddingClient:
    """Build the embedding client for the configured provider.

    Raises ``ValueError`` on an unknown provider so misconfig fails loud at
    startup rather than at first embed call.
    """
    provider = config.embedding_provider
    if provider == "fastembed":
        return FastEmbedEmbeddingClient(
            model=config.embed_model_id,
            budget=budget,
            cache_dir=config.embed_cache_dir,
        )
    if provider == "openai":
        openai_sdk = AsyncOpenAI(
            api_key=config.openai_api_key.get_secret_value(),
        )
        return OpenAIEmbeddingClient(
            sdk=openai_sdk,
            budget=budget,
            model=config.embed_model_id,
        )
    valid = ("fastembed", "openai")
    msg = f"unknown embedding_provider {provider!r}; valid choices: {valid}"
    raise ValueError(msg)
