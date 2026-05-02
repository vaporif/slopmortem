"""EmbeddingClient Protocol: minimum async embedding contract (batch in, vectors out)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class EmbeddingResult:
    """Result of a single embed call: per-text vectors, token count, and settled cost."""

    vectors: list[list[float]]
    n_tokens: int
    cost_usd: float


@runtime_checkable
class EmbeddingClient(Protocol):
    """Async embedding contract that the OpenAI and fake backends implement."""

    async def embed(self, texts: list[str], *, model: str | None = None) -> EmbeddingResult: ...
