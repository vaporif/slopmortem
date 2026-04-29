"""Smoke tests for corpus.schema (re-exports) and corpus.store (Corpus protocol).

Both modules are type-only — callers import under ``TYPE_CHECKING``, so they
never run and would show 0% coverage. These tests pin the public surface and
exercise runtime ``isinstance`` against the Protocol.
"""

from slopmortem.corpus import schema, store
from slopmortem.models import AliasEdge, MergeState, RawEntry


def test_schema_reexports_resolve_to_models():
    assert schema.AliasEdge is AliasEdge
    assert schema.MergeState is MergeState
    assert schema.RawEntry is RawEntry
    assert set(schema.__all__) == {"AliasEdge", "MergeState", "RawEntry"}


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

    assert isinstance(_Impl(), store.Corpus)


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

    assert not isinstance(_Partial(), store.Corpus)
