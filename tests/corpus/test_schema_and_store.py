"""Smoke tests for the Corpus protocol re-exported by the corpus façade.

The Corpus protocol lives in `_store` and is type-only (callers import under
TYPE_CHECKING). These tests pin the public surface and exercise runtime
isinstance against the Protocol so a refactor can't silently break it.
"""

from __future__ import annotations

from slopmortem.corpus import Corpus


def test_corpus_protocol_accepts_full_implementation():
    class _Impl:
        async def query(  # noqa: PLR0913 — mirrors the Corpus Protocol's kwargs-only surface
            self,
            *,
            dense,
            sparse,
            facets,
            cutoff_iso,
            strict_deaths,
            k_retrieve,
        ):
            return []

        async def get_post_mortem(self, canonical_id):
            return ""

        async def search_corpus(self, q, facets=None):
            return []

    assert isinstance(_Impl(), Corpus)


def test_corpus_protocol_rejects_partial_implementation():
    class _Partial:
        async def query(  # noqa: PLR0913 — mirrors the Corpus Protocol's kwargs-only surface
            self,
            *,
            dense,
            sparse,
            facets,
            cutoff_iso,
            strict_deaths,
            k_retrieve,
        ):
            return []

    assert not isinstance(_Partial(), Corpus)
