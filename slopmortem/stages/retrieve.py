"""Retrieve stage: embed the description and delegate to :meth:`Corpus.query`.

Thin orchestrator. The hybrid-retrieval contract (FormulaQuery + RRF +
recency-branch filter + collapse-to-parents + alias-graph dedup) lives in
:meth:`slopmortem.corpus.qdrant_store.QdrantCorpus.query` — this stage embeds
the user's description (dense via :class:`EmbeddingClient`, sparse via
``embed_sparse.encode``) and forwards every other knob through unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from slopmortem.corpus.store import Corpus
    from slopmortem.llm.embedding_client import EmbeddingClient
    from slopmortem.models import Candidate, Facets

type SparseEncoder = Callable[[str], dict[int, float]]


async def retrieve(  # noqa: PLR0913 — every dependency is required at the call site
    *,
    description: str,
    facets: Facets,
    corpus: Corpus,
    embedding_client: EmbeddingClient,
    cutoff_iso: str | None,
    strict_deaths: bool,
    k_retrieve: int,
    sparse_encoder: SparseEncoder | None = None,
) -> list[Candidate]:
    """Embed *description* and run hybrid retrieve against *corpus*.

    Args:
        description: User's pitch text — both the dense and sparse query
            inputs come from this verbatim. No HyDE expansion (see spec
            line 213; rerank slack absorbs the modality gap).
        facets: Soft-boost facets from the facet-extract stage; ``"other"``
            values are skipped inside :meth:`Corpus.query`.
        corpus: Read-side :class:`Corpus` impl. Production is
            :class:`QdrantCorpus`.
        embedding_client: Async dense embedder (OpenAI in production,
            :class:`FakeEmbeddingClient` in tests).
        cutoff_iso: ISO-8601 lower bound for the recency filter, or ``None``
            to disable the filter entirely.
        strict_deaths: When ``True``, Corpus retains only docs with a known
            ``failure_date`` ≥ ``cutoff_iso``.
        k_retrieve: Final number of parent candidates to return. Caller
            sets this from ``Config.K_retrieve``.
        sparse_encoder: Override the BM25 sparse encoder. ``None`` lazy-loads
            the production fastembed model on first call. Tests pass a
            no-op stub so they don't trigger the ~150 MB ONNX download
            (mirrors the same pattern used by ``ingest()``).

    Returns:
        Up to ``k_retrieve`` :class:`Candidate` objects in descending
        retrieval-score order.
    """
    if sparse_encoder is None:
        from slopmortem.corpus.embed_sparse import encode as _default_encode  # noqa: PLC0415

        sparse_encoder = _default_encode

    embed_result = await embedding_client.embed([description])
    [dense] = embed_result.vectors
    sparse = sparse_encoder(description)
    return await corpus.query(
        dense=dense,
        sparse=sparse,
        facets=facets,
        cutoff_iso=cutoff_iso,
        strict_deaths=strict_deaths,
        k_retrieve=k_retrieve,
    )
