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
        years_filter: int | None,
        strict_deaths: bool,
        k_retrieve: int,
    ) -> list[Candidate]:
        """Hybrid retrieve the top-K candidates by dense and sparse vectors, filtered by facets."""
        ...

    async def get_post_mortem(self, canonical_id: str) -> str:
        """Fetch the full canonical post-mortem text for *canonical_id*."""
        ...

    async def search_corpus(
        self, q: str, facets: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:  # type: ignore[explicit-any]  # Protocol — implementations vary
        """Plain-text search the corpus for additional candidates, optionally filtered by facets."""
        ...
