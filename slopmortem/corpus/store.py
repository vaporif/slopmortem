"""Corpus protocol: the read-side interface that stored docs are queried through."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from slopmortem.models import Candidate, Facets


@runtime_checkable
class Corpus(Protocol):
    """Read-side protocol over the persisted candidate corpus, real (Qdrant) or fake."""

    async def query(  # noqa: PLR0913 — Protocol method signature is the public contract
        self,
        *,
        dense: list[float],
        sparse: dict[int, float],
        facets: Facets,
        cutoff_iso: str | None,
        strict_deaths: bool,
        k_retrieve: int,
    ) -> list[Candidate]:
        """Hybrid retrieve the top-K candidates by dense and sparse vectors, filtered by facets.

        Args:
            dense: Dense query vector.
            sparse: Sparse query vector as ``{token_id: weight}``.
            facets: Soft-boost facets; ``"other"`` values must be skipped.
            cutoff_iso: ISO-8601 lower bound for the recency filter, or
                ``None`` to disable the filter entirely.
            strict_deaths: When ``True``, only retrieve docs with a known
                ``failure_date >= cutoff_iso``.
            k_retrieve: Final number of parent candidates to return.
        """
        ...

    async def get_post_mortem(self, canonical_id: str) -> str:
        """Fetch the full canonical post-mortem text for *canonical_id*."""
        ...

    async def search_corpus(
        self, q: str, facets: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:  # type: ignore[explicit-any]  # Protocol — implementations vary
        """Plain-text search the corpus for additional candidates, optionally filtered by facets."""
        ...
