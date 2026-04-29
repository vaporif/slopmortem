"""EmbeddingClient Protocol — minimum async embedding contract (one batch in, vectors out)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class EmbeddingResult:
    """Single embed call result — per-text vectors plus token count and settled cost."""

    vectors: list[list[float]]
    n_tokens: int
    cost_usd: float


@runtime_checkable
class EmbeddingClient(Protocol):
    """The async embedding contract every backend (OpenAI, fake) implements."""

    async def embed(self, texts: list[str], *, model: str | None = None) -> EmbeddingResult:
        """Embed *texts* with the configured (or overridden) model and return vectors."""
        ...
