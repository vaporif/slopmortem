"""EmbeddingClient Protocol: minimum async embedding contract (batch in, vectors out)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    n_tokens: int
    cost_usd: float


@runtime_checkable
class EmbeddingClient(Protocol):
    async def embed(self, texts: list[str], *, model: str | None = None) -> EmbeddingResult: ...
