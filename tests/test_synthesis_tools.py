"""Synthesis tool unit tests (plan §9.1).

The corpus tool functions ``_get_post_mortem`` / ``_search_corpus`` delegate
through the :class:`slopmortem.corpus.store.Corpus` protocol. These tests
exercise the delegation wiring with a lightweight in-memory fake corpus —
the live Qdrant integration shape is covered separately by
``tests/stages/test_retrieve.py`` (gated on ``requires_qdrant``).

Using a fake here (vs. the live ``fixture_corpus`` from the retrieve tests)
keeps Task 9 unit-testable without a Qdrant service: the contract under
test is the tool function -> Corpus protocol delegation, not Qdrant
behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from slopmortem.corpus.tools_impl import _get_post_mortem, _search_corpus, _set_corpus

if TYPE_CHECKING:
    from slopmortem.models import Candidate, Facets


class _FakeCorpus:
    """Minimal Corpus stand-in: only ``get_post_mortem`` / ``search_corpus`` are read by Task 9.

    A no-op ``query`` is supplied so the structural :class:`Corpus` protocol
    is satisfied for ``_set_corpus``'s signature.
    """

    def __init__(
        self,
        *,
        canonical: dict[str, str] | None = None,
        hits: list[dict[str, Any]] | None = None,
    ) -> None:
        self._canonical = canonical or {}
        self._hits = hits or []
        self.last_query: tuple[str, dict[str, str] | None] | None = None

    async def query(  # noqa: PLR0913
        self,
        *,
        dense: list[float],
        sparse: dict[int, float],
        facets: Facets,
        cutoff_iso: str | None,
        strict_deaths: bool,
        k_retrieve: int,
    ) -> list[Candidate]:
        _ = (dense, sparse, facets, cutoff_iso, strict_deaths, k_retrieve)
        return []

    async def get_post_mortem(self, canonical_id: str) -> str:
        return self._canonical.get(canonical_id, "")

    async def search_corpus(
        self,
        q: str,
        facets: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        self.last_query = (q, facets)
        return list(self._hits)


@pytest.fixture
def fixture_corpus():
    """In-memory fake corpus pre-loaded with a single Acme post-mortem and one search hit."""
    corpus = _FakeCorpus(
        canonical={"acme.com": "# Acme\n\nAcme failed because of scrap-metal margins."},
        hits=[
            {
                "canonical_id": "rivals.com",
                "name": "Rivals Scrap",
                "summary": "Rivals scrap-metal marketplace pivoted away.",
                "body": "Long body text about industrial scrap aggregation.",
                "score": 0.42,
            }
        ],
    )
    _set_corpus(corpus)
    try:
        yield corpus
    finally:
        # Reset module-level binding so other tests don't observe our fake.
        import slopmortem.corpus.tools_impl as ti  # noqa: PLC0415

        ti._corpus = None


async def test_get_post_mortem_reads_canonical(fixture_corpus):
    text = await _get_post_mortem("acme.com")
    assert "Acme" in text or len(text) > 0


async def test_search_corpus_returns_hits(fixture_corpus):
    hits = await _search_corpus("scrap metal", facets={"sector": "logistics_supply_chain"})
    assert len(hits) > 0
