"""Retrieve stage: embed the description and delegate to :meth:`Corpus.query`.

Thin orchestrator. The hybrid-retrieval contract (FormulaQuery + RRF +
recency-branch filter + collapse-to-parents + alias-graph dedup) lives in
:meth:`slopmortem.corpus.qdrant_store.QdrantCorpus.query`. This stage embeds
the user's description (dense via :class:`EmbeddingClient`, sparse via
``embed_sparse.encode``) and forwards every other knob unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lmnr import Laminar, observe

if TYPE_CHECKING:
    from collections.abc import Callable

    from slopmortem.corpus.store import Corpus
    from slopmortem.llm.embedding_client import EmbeddingClient
    from slopmortem.models import Candidate, Facets

type SparseEncoder = Callable[[str], dict[int, float]]


# ``ignore_output=True`` drops the auto-captured Candidate output; a redacted
# ``(canonical_id, score, name, facets, slop_score)`` projection gets re-attached
# below. ``ignore_inputs=["corpus"]`` keeps the corpus handle out of the trace
# (test fakes inline their candidate set; production stores serialize the
# client handle, neither of which is useful trace content). Body bytes never
# cross the trace boundary.
@observe(name="stage.retrieve", ignore_output=True, ignore_inputs=["corpus"])
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
        description: User's pitch text. Both dense and sparse query inputs
            come from this verbatim. No HyDE expansion — rerank slack absorbs
            the modality gap.
        facets: Soft-boost facets from the facet-extract stage. ``"other"``
            values are skipped inside :meth:`Corpus.query`.
        corpus: Read-side :class:`Corpus` impl. Production is
            :class:`QdrantCorpus`.
        embedding_client: Async dense embedder (OpenAI in production,
            :class:`FakeEmbeddingClient` in tests).
        cutoff_iso: ISO-8601 lower bound for the recency filter, or ``None``
            to disable filtering entirely.
        strict_deaths: When ``True``, Corpus keeps only docs with a known
            ``failure_date`` ≥ ``cutoff_iso``.
        k_retrieve: Final number of parent candidates to return. Caller sets
            this from ``Config.K_retrieve``.
        sparse_encoder: BM25 sparse encoder override. ``None`` lazy-loads the
            production fastembed model on first call. Tests pass a no-op stub
            so they don't trigger the ~150 MB ONNX download (same pattern as
            ``ingest()``).

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
    candidates = await corpus.query(
        dense=dense,
        sparse=sparse,
        facets=facets,
        cutoff_iso=cutoff_iso,
        strict_deaths=strict_deaths,
        k_retrieve=k_retrieve,
    )
    Laminar.set_span_attributes(
        {
            "candidates": [
                {
                    "canonical_id": c.canonical_id,
                    "score": c.score,
                    "name": c.payload.name,
                    "facets": c.payload.facets.model_dump(),
                    "slop_score": c.payload.slop_score,
                }
                for c in candidates
            ],
        }
    )
    return candidates
