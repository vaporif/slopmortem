"""Integration tests for the retrieve stage + QdrantCorpus.query FormulaQuery impl.

The four cases below are gated on ``@pytest.mark.requires_qdrant`` and exercise
the live FormulaQuery + recency-branch + facet-skip-other behavior end-to-end
against a Qdrant service on ``localhost:6333``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from slopmortem.corpus.qdrant_store import QdrantCorpus, ensure_collection
from slopmortem.llm.fake_embeddings import FakeEmbeddingClient
from slopmortem.llm.openai_embeddings import EMBED_DIMS
from slopmortem.models import CandidatePayload, Facets
from slopmortem.stages.retrieve import retrieve

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from qdrant_client import AsyncQdrantClient


_DIM = EMBED_DIMS["text-embedding-3-small"]


def _facets(**overrides: object) -> Facets:
    base: dict[str, object] = {
        "sector": "fintech",
        "business_model": "b2b_saas",
        "customer_type": "smb",
        "geography": "us",
        "monetization": "subscription_recurring",
    }
    base.update(overrides)
    return Facets(**base)  # type: ignore[arg-type]


def _payload(*, name: str, summary: str = "summary", facets: Facets | None = None,
             founding_date: date | None = None, failure_date: date | None = None,
             founding_unknown: bool = False, failure_unknown: bool = False,
             text_id: str = "abcdef0123456789") -> CandidatePayload:
    return CandidatePayload(
        name=name,
        summary=summary,
        body="body",
        facets=facets or _facets(),
        founding_date=founding_date,
        failure_date=failure_date,
        founding_date_unknown=founding_unknown,
        failure_date_unknown=failure_unknown,
        provenance="curated_real",
        slop_score=0.0,
        sources=["curated:0"],
        text_id=text_id,
    )


def _to_iso(d: date | None) -> str | None:
    if d is None:
        return None
    return datetime(d.year, d.month, d.day).isoformat() + "Z"


def _build_payload_dict(canonical_id: str, payload: CandidatePayload) -> dict[str, object]:
    """Render a CandidatePayload into the Qdrant payload dict, with ISO dates."""
    pd = payload.model_dump(mode="json")
    # Qdrant DatetimeRange compares ISO-8601 strings.
    pd["founding_date"] = _to_iso(payload.founding_date)
    pd["failure_date"] = _to_iso(payload.failure_date)
    pd["canonical_id"] = canonical_id
    pd["chunk_idx"] = 0
    return pd


async def _seed(
    client: AsyncQdrantClient,
    collection: str,
    *,
    canonical_id: str,
    dense: list[float],
    sparse: dict[int, float],
    payload: CandidatePayload,
) -> None:
    from qdrant_client.models import PointStruct, SparseVector  # noqa: PLC0415

    pt = PointStruct(
        id=uuid.uuid5(uuid.NAMESPACE_URL, f"{canonical_id}:0").hex,
        vector={
            "dense": dense,
            "sparse": SparseVector(
                indices=list(sparse.keys()),
                values=list(sparse.values()),
            ),
        },
        payload=_build_payload_dict(canonical_id, payload),
    )
    await client.upsert(collection_name=collection, points=[pt], wait=True)


@pytest_asyncio.fixture
async def fixture_corpus(qdrant_client, tmp_path) -> AsyncIterator[
    tuple[QdrantCorpus, str, FakeEmbeddingClient]
]:
    """Create a fresh collection + QdrantCorpus instance scoped to one test.

    Uses a generous ``facet_boost=10.0`` so a 4-facet match overwhelms RRF
    tie-breaking noise (Qdrant tie-breaks identical RRF positions by point
    id, not symmetrically — see qdrant#5182). The production value 0.01
    is verified separately by reading-side unit tests; this fixture exists
    to assert the integration shape of the FormulaQuery wiring, not the
    calibrated value.
    """
    name = f"test_retrieve_{uuid.uuid4().hex[:8]}"
    if await qdrant_client.collection_exists(name):
        await qdrant_client.delete_collection(name)
    await ensure_collection(qdrant_client, name, dim=_DIM)
    corpus = QdrantCorpus(
        client=qdrant_client,
        collection=name,
        post_mortems_root=tmp_path,
        facet_boost=10.0,
    )
    embed = FakeEmbeddingClient(model="text-embedding-3-small")
    try:
        yield corpus, name, embed
    finally:
        await qdrant_client.delete_collection(name)


@pytest.mark.requires_qdrant
async def test_retrieve_with_facet_boost_outranks_unboosted(qdrant_client, fixture_corpus):
    """Three docs at the same dense+sparse score: full-facet match must rank first."""
    corpus, name, embed = fixture_corpus
    # Use the same query embedding for every seed so the dense $score is identical;
    # the only differentiator is the facet-match boost.
    description = "marketplace for industrial scrap metal"
    [qvec] = (await embed.embed([description])).vectors

    full = _facets(sector="fintech", business_model="b2b_saas",
                   customer_type="smb", geography="us",
                   monetization="subscription_recurring")
    partial = _facets(sector="fintech", business_model="b2b_saas",
                      customer_type="enterprise", geography="eu",
                      monetization="ad_revenue")
    none_match = _facets(sector="healthtech", business_model="b2c_subscription",
                         customer_type="enterprise", geography="eu",
                         monetization="ad_revenue")

    sparse: dict[int, float] = {1: 1.0}
    today = date(2024, 1, 1)
    for cid, facets in (
        ("full", full),
        ("partial", partial),
        ("none", none_match),
    ):
        await _seed(
            qdrant_client, name,
            canonical_id=cid,
            dense=qvec,
            sparse=sparse,
            payload=_payload(
                name=cid, facets=facets,
                failure_date=today, founding_date=date(2020, 1, 1),
            ),
        )

    candidates = await retrieve(
        description=description,
        facets=full,
        corpus=corpus,
        embedding_client=embed,
        cutoff_iso="2000-01-01T00:00:00Z",
        strict_deaths=False,
        k_retrieve=10,
        sparse_encoder=lambda _t: {1: 1.0},
    )

    ids = [c.canonical_id for c in candidates]
    assert ids[0] == "full"
    assert "partial" in ids
    assert ids.index("full") < ids.index("partial")


@pytest.mark.requires_qdrant
async def test_recency_branch_C_passthrough_undated(qdrant_client, fixture_corpus):
    """A doc with both dates unknown must surface under non-strict mode (branch C)."""
    corpus, name, embed = fixture_corpus
    description = "saas startup"
    [qvec] = (await embed.embed([description])).vectors

    facets = _facets()
    await _seed(
        qdrant_client, name,
        canonical_id="undated",
        dense=qvec,
        sparse={1: 1.0},
        payload=_payload(
            name="undated", facets=facets,
            founding_date=None, failure_date=None,
            founding_unknown=True, failure_unknown=True,
        ),
    )

    candidates = await retrieve(
        description=description,
        facets=facets,
        corpus=corpus,
        embedding_client=embed,
        cutoff_iso="2020-01-01T00:00:00Z",
        strict_deaths=False,
        k_retrieve=10,
        sparse_encoder=lambda _t: {1: 1.0},
    )
    ids = [c.canonical_id for c in candidates]
    assert "undated" in ids


@pytest.mark.requires_qdrant
async def test_strict_deaths_filters_unknown(qdrant_client, fixture_corpus):
    """Strict mode keeps only docs with a known failure_date >= cutoff."""
    corpus, name, embed = fixture_corpus
    description = "saas"
    [qvec] = (await embed.embed([description])).vectors

    facets = _facets()
    # branch A doc — should appear.
    await _seed(
        qdrant_client, name,
        canonical_id="dated",
        dense=qvec,
        sparse={1: 1.0},
        payload=_payload(
            name="dated", facets=facets,
            founding_date=date(2018, 1, 1), failure_date=date(2023, 6, 1),
        ),
    )
    # branch C doc — should NOT appear under --strict-deaths.
    await _seed(
        qdrant_client, name,
        canonical_id="undated",
        dense=qvec,
        sparse={1: 1.0},
        payload=_payload(
            name="undated", facets=facets,
            founding_date=None, failure_date=None,
            founding_unknown=True, failure_unknown=True,
        ),
    )

    candidates = await retrieve(
        description=description,
        facets=facets,
        corpus=corpus,
        embedding_client=embed,
        cutoff_iso="2020-01-01T00:00:00Z",
        strict_deaths=True,
        k_retrieve=10,
        sparse_encoder=lambda _t: {1: 1.0},
    )
    ids = [c.canonical_id for c in candidates]
    assert "dated" in ids
    assert "undated" not in ids


@pytest.mark.requires_qdrant
async def test_other_facet_does_not_boost(qdrant_client, fixture_corpus):
    """Facet value ``"other"`` must NOT enter the FormulaQuery boost condition.

    If ``"other"`` were included, a doc that bucketed every facet to ``other``
    would match the boost and outrank a doc with no field overlap; we assert
    boost equality instead, by querying with all-other and confirming the
    ranking is independent of which facet bucket the candidate landed in.
    """
    corpus, name, embed = fixture_corpus
    description = "fuzzy mystery business"
    [qvec] = (await embed.embed([description])).vectors

    real_facets = _facets()
    other_facets = _facets(sector="other", business_model="other",
                           customer_type="other", geography="other", monetization="other")
    today = date(2023, 1, 1)
    # Doc whose payload has every facet bucketed to "other".
    await _seed(
        qdrant_client, name,
        canonical_id="all_other",
        dense=qvec,
        sparse={1: 1.0},
        payload=_payload(
            name="all_other", facets=other_facets,
            failure_date=today, founding_date=date(2018, 1, 1),
        ),
    )
    # Doc with concrete facets that DO match the (real_facets) boost set.
    await _seed(
        qdrant_client, name,
        canonical_id="real_match",
        dense=qvec,
        sparse={1: 1.0},
        payload=_payload(
            name="real_match", facets=real_facets,
            failure_date=today, founding_date=date(2018, 1, 1),
        ),
    )

    # Query with all-"other" facets: the FormulaQuery must skip every "other"
    # entry, leaving NO boost condition active. With ``facet_boost=10.0``
    # (see fixture), an active 5-facet boost would yield score >= ~50.
    # We assert no candidate's score crosses the no-boost ceiling — Qdrant's
    # RRF $score caps at 1.0 with two channels — regardless of which facet
    # bucket the candidate landed in. This confirms "other" did not enter
    # the FormulaQuery condition.
    candidates = await retrieve(
        description=description,
        facets=other_facets,
        corpus=corpus,
        embedding_client=embed,
        cutoff_iso="2000-01-01T00:00:00Z",
        strict_deaths=False,
        k_retrieve=10,
        sparse_encoder=lambda _t: {1: 1.0},
    )
    ids = [c.canonical_id for c in candidates]
    assert "all_other" in ids
    assert "real_match" in ids
    # Sanity ceiling: with no boost active, no candidate's score should
    # exceed the RRF cap. If "other" had entered the boost FilterCondition,
    # "all_other" alone would have matched all 5 facet equality checks and
    # earned 5 * facet_boost = 50.0 on top of the RRF $score.
    score_by_id = {c.canonical_id: c.score for c in candidates}
    assert score_by_id["all_other"] < 5.0
    assert score_by_id["real_match"] < 5.0
    _ = Path  # silence unused-import lint when this test runs alone
