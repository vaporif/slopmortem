from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from slopmortem.llm.embedding_client import EmbeddingResult
from slopmortem.llm.openai_embeddings import EMBED_DIMS


@dataclass
class _EmbedCall:
    texts: list[str]
    model: str


class FakeEmbeddingClient:
    """Deterministic in-memory EmbeddingClient for tests.

    Vectors are derived from sha256(text) so the same input produces the same
    vector across runs and processes — stable fixtures without recording.
    """

    def __init__(
        self,
        *,
        model: str,
        cost_per_call: float = 0.0,
        calls: list[_EmbedCall] | None = None,
    ):
        if model not in EMBED_DIMS:
            raise ValueError(
                f"unknown embed model {model!r}; add it to EMBED_DIMS"
            )
        self.model = model
        self.cost_per_call = cost_per_call
        self.calls: list[_EmbedCall] = calls if calls is not None else []

    @property
    def dim(self) -> int:
        return EMBED_DIMS[self.model]

    async def embed(
        self, texts: list[str], *, model: str | None = None
    ) -> EmbeddingResult:
        eff_model = model or self.model
        self.calls.append(_EmbedCall(texts=list(texts), model=eff_model))
        dim = EMBED_DIMS[eff_model] if model is not None else self.dim
        vectors = [_sha_vector(t, dim) for t in texts]
        return EmbeddingResult(
            vectors=vectors,
            n_tokens=0,
            cost_usd=self.cost_per_call * len(texts),
        )


def _sha_vector(text: str, dim: int) -> list[float]:
    """Expand sha256(text) into ``dim`` floats in [-1, 1].

    Repeats the digest with a counter suffix until we have enough bytes,
    then maps each byte to a float in [-1, 1].
    """
    out: list[int] = []
    counter = 0
    while len(out) < dim:
        h = hashlib.sha256(f"{counter}:{text}".encode()).digest()
        out.extend(h)
        counter += 1
    return [(b / 127.5) - 1.0 for b in out[:dim]]
