"""Smoke tests for corpus.schema (re-exports) and corpus.store (Corpus protocol).

These modules are type-only surfaces — every caller imports them under
``TYPE_CHECKING``, so they never execute at runtime and would otherwise show
as 0% coverage. Tests here pin the public surface and exercise the runtime
``isinstance`` behaviour of the Protocol.
"""

from __future__ import annotations

from slopmortem.corpus import schema, store
from slopmortem.models import AliasEdge, MergeState, RawEntry


def test_schema_reexports_resolve_to_models():
    assert schema.AliasEdge is AliasEdge
    assert schema.MergeState is MergeState
    assert schema.RawEntry is RawEntry
    assert set(schema.__all__) == {"AliasEdge", "MergeState", "RawEntry"}


def test_corpus_protocol_accepts_full_implementation():
    class _Impl:
        async def query(
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
        async def query(
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
