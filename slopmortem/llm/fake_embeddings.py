"""Deterministic in-memory EmbeddingClient stub keyed on sha256(text)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from slopmortem.llm.cassettes import NoCannedEmbeddingError, embed_cassette_key
from slopmortem.llm.embedding_client import EmbeddingResult
from slopmortem.llm.openai_embeddings import EMBED_DIMS

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass
class _EmbedCall:
    texts: list[str]
    model: str


class FakeEmbeddingClient:
    """Deterministic in-memory EmbeddingClient for tests.

    When ``canned`` is None (default) vectors come from sha256(text), so the
    same input always produces the same vector across runs and processes.
    When ``canned`` is supplied, lookups are strict on
    ``(model, text_hash)`` (matching :func:`embed_cassette_key`); a miss
    raises :class:`NoCannedEmbeddingError`.
    """

    def __init__(  # noqa: D107
        self,
        *,
        model: str,
        cost_per_call: float = 0.0,
        canned: Mapping[tuple[str, str], list[float]] | None = None,
        calls: list[_EmbedCall] | None = None,
    ) -> None:
        if model not in EMBED_DIMS:
            msg = f"unknown embed model {model!r}; add it to EMBED_DIMS"
            raise ValueError(msg)
        self.model = model
        self.cost_per_call = cost_per_call
        self._canned = canned
        self.calls: list[_EmbedCall] = calls if calls is not None else []

    @property
    def dim(self) -> int:
        """Vector dimensionality for the configured embedding model."""
        return EMBED_DIMS[self.model]

    async def embed(self, texts: list[str], *, model: str | None = None) -> EmbeddingResult:
        """Return canned vectors when configured; otherwise sha256-derived deterministic vectors."""
        eff_model = model or self.model
        self.calls.append(_EmbedCall(texts=list(texts), model=eff_model))
        if self._canned is not None:
            vectors: list[list[float]] = []
            for text in texts:
                key = embed_cassette_key(text=text, model=eff_model)
                if key not in self._canned:
                    msg = (
                        f"no canned embedding for key={key!r}; "
                        f"recorded keys: {sorted(self._canned)}"
                    )
                    raise NoCannedEmbeddingError(msg)
                vectors.append(list(self._canned[key]))
            return EmbeddingResult(vectors=vectors, n_tokens=0, cost_usd=0.0)
        dim = EMBED_DIMS[eff_model] if model is not None else self.dim
        vectors = [_sha_vector(t, dim) for t in texts]
        return EmbeddingResult(
            vectors=vectors,
            n_tokens=0,
            cost_usd=self.cost_per_call * len(texts),
        )


def _sha_vector(text: str, dim: int) -> list[float]:
    """Expand sha256(text) into ``dim`` floats in [-1, 1].

    Repeats the hash with a counter suffix until there are enough bytes,
    then maps each byte to a float in [-1, 1].
    """
    out: list[int] = []
    counter = 0
    while len(out) < dim:
        h = hashlib.sha256(f"{counter}:{text}".encode()).digest()
        out.extend(h)
        counter += 1
    return [(b / 127.5) - 1.0 for b in out[:dim]]
