from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from slopmortem.models import Candidate, Facets


@runtime_checkable
class Corpus(Protocol):
    async def query(
        self,
        *,
        dense: list[float],
        sparse: dict[int, float],
        facets: Facets,
        years_filter: int | None,
        strict_deaths: bool,
        k_retrieve: int,
    ) -> list[Candidate]: ...

    async def get_post_mortem(self, canonical_id: str) -> str: ...

    async def search_corpus(
        self, q: str, facets: dict[str, str] | None = None
    ) -> list[dict[str, Any]]: ...
