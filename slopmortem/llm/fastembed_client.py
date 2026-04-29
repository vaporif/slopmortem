"""Local ONNX embedding client backed by fastembed; mirrors the OpenAI client contract."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from anyio import to_thread

from slopmortem.llm.embedding_client import EmbeddingResult
from slopmortem.llm.openai_embeddings import EMBED_DIMS

if TYPE_CHECKING:
    from pathlib import Path

    from slopmortem.budget import Budget


class FastEmbedEmbeddingClient:
    """ONNX-backed EmbeddingClient that runs locally and settles zero against the budget."""

    def __init__(
        self,
        *,
        model: str,
        budget: Budget,
        cache_dir: Path | None = None,
    ) -> None:
        """Bind the model and budget; defer fastembed import and model load until first embed."""
        if model not in EMBED_DIMS:
            msg = f"unknown embed model {model!r}; add it to EMBED_DIMS"
            raise ValueError(msg)
        self.model = model
        self._budget = budget
        self._cache_dir = cache_dir
        self._te: object | None = None  # fastembed.TextEmbedding instance, lazy

    @property
    def dim(self) -> int:
        """Vector dimensionality for the configured embedding model."""
        return EMBED_DIMS[self.model]

    async def _ensure_loaded(self) -> object:
        """Materialize the fastembed model on first use; idempotent."""
        if self._te is not None:
            return self._te
        self._te = await to_thread.run_sync(self._load_sync)
        return self._te

    def _load_sync(self) -> object:
        from fastembed import TextEmbedding  # noqa: PLC0415 — heavy import, defer

        cache_dir = str(self._cache_dir) if self._cache_dir is not None else None
        try:
            return TextEmbedding(
                model_name=self.model,
                cache_dir=cache_dir,
                lazy_load=True,
            )
        except Exception as exc:
            msg = (
                f"fastembed model {self.model!r} failed to load: {exc}; "
                f"try running 'slopmortem embed-prefetch'"
            )
            raise RuntimeError(msg) from exc

    async def embed(self, texts: list[str], *, model: str | None = None) -> EmbeddingResult:
        """Embed *texts* locally; budget reserve/settle 0.0 for contract symmetry."""
        if model is not None and model != self.model:
            msg = (
                f"FastEmbedEmbeddingClient was constructed with {self.model!r}; "
                f"per-call model override {model!r} is not supported"
            )
            raise ValueError(msg)
        if not texts:
            return EmbeddingResult(vectors=[], n_tokens=0, cost_usd=0.0)

        rid = await self._budget.reserve(0.0)
        try:
            te = await self._ensure_loaded()
            vectors, n_tokens = await to_thread.run_sync(self._embed_sync, te, texts)
        finally:
            await self._budget.settle(rid, 0.0)
        return EmbeddingResult(vectors=vectors, n_tokens=n_tokens, cost_usd=0.0)

    @staticmethod
    def _embed_sync(te: object, texts: list[str]) -> tuple[list[list[float]], int]:
        """Run fastembed inference + tokenizer count on a worker thread.

        Vectors are L2-normalized before return so cosine == dot in Qdrant.
        fastembed routes ``nomic-ai/nomic-embed-text-v1.5`` through
        ``PooledEmbedding`` (mean pooling without normalization), so we
        normalize here.
        """
        gen = te.embed(texts)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownVariableType]
        vectors: list[list[float]] = []
        for v in gen:  # pyright: ignore[reportUnknownVariableType]
            arr = np.asarray(v, dtype=np.float32)
            norm = float(np.linalg.norm(arr))
            if norm > 0.0:
                arr = arr / norm
            vectors.append(arr.tolist())  # pyright: ignore[reportAny] — numpy ndarray.tolist() typed as Any
        n_tokens = int(te.token_count(texts))  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]
        return vectors, n_tokens
