# pyright: reportAny=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false
"""BM25 sparse embedder. Thin wrapper around :mod:`fastembed`.

The Qdrant collection needs ``Modifier.IDF`` for the fastembed BM25 model;
without IDF, sparse retrieval falls back to raw token-frequency matching.
The model loads lazily on the first ``encode`` call so ONNX startup doesn't
dominate test collection time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastembed import SparseTextEmbedding

_MODEL_NAME = "Qdrant/bm25"
_model: SparseTextEmbedding | None = None


def _get_model() -> SparseTextEmbedding:
    global _model  # noqa: PLW0603 — single-process lazy singleton
    if _model is None:
        from fastembed import SparseTextEmbedding  # noqa: PLC0415

        _model = SparseTextEmbedding(model_name=_MODEL_NAME)
    return _model


def encode(text: str) -> dict[int, float]:
    """Return a sparse vector as a ``{token_id: weight}`` dict for *text*."""
    model = _get_model()
    [emb] = list(model.embed([text]))
    return dict(zip(emb.indices.tolist(), emb.values.tolist(), strict=True))
