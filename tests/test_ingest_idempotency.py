"""Idempotency: running ingest twice on the same fixture creates no duplicate Qdrant points."""

import json
from datetime import UTC, datetime

import pytest

from conftest import llm_canned_key
from slopmortem.budget import Budget
from slopmortem.config import Config
from slopmortem.corpus.merge import MergeJournal
from slopmortem.ingest import FakeSlopClassifier, InMemoryCorpus, ingest
from slopmortem.llm.fake import FakeLLMClient, FakeResponse
from slopmortem.llm.fake_embeddings import FakeEmbeddingClient
from slopmortem.llm.prompts import render_prompt
from slopmortem.models import RawEntry

_HAIKU = "anthropic/claude-haiku-4.5"
_BODY = "Acme was a marketplace startup that ran out of money. " * 30


def _stub_sparse(_text: str) -> dict[int, float]:
    return {0: 1.0}


def _facets() -> str:
    return json.dumps(
        {
            "sector": "retail_ecommerce",
            "business_model": "b2b_marketplace",
            "customer_type": "smb",
            "geography": "us",
            "monetization": "transaction_fee",
            "founding_year": 2018,
            "failure_year": 2021,
        }
    )


def _canned() -> dict[tuple[str, str, str], FakeResponse]:
    """Build canned entries for one ingest of `_BODY`: cache-warm + facet + fanout summarize."""
    facets_resp = FakeResponse(
        text=_facets(),
        cache_creation_tokens=1000,
        cache_read_tokens=5000,
    )
    summary_resp = FakeResponse(
        text="Acme summary here.",
        cache_creation_tokens=0,
        cache_read_tokens=5000,
    )
    facet_prompt = render_prompt("facet_extract", description=_BODY)
    summarize_warm_prompt = render_prompt("summarize", body=_BODY[:1000], source_id="warm")
    summarize_fanout_prompt = render_prompt("summarize", body=_BODY, source_id="")
    return {
        llm_canned_key("facet_extract", model=_HAIKU, prompt=facet_prompt): facets_resp,
        llm_canned_key("summarize", model=_HAIKU, prompt=summarize_warm_prompt): summary_resp,
        llm_canned_key("summarize", model=_HAIKU, prompt=summarize_fanout_prompt): summary_resp,
    }


def _entry() -> RawEntry:
    return RawEntry(
        source="curated",
        source_id="acme",
        url="https://acme.com",
        raw_html=None,
        markdown_text=_BODY,
        fetched_at=datetime(2026, 4, 28, tzinfo=UTC),
    )


class _OneShotSource:
    def __init__(self) -> None:
        self.entries = [_entry()]

    async def fetch(self):
        for e in self.entries:
            yield e


@pytest.fixture
def cfg() -> Config:
    return Config(max_cost_usd_per_ingest=100.0, ingest_concurrency=20)


async def test_ingest_twice_no_duplicate_points(tmp_path, cfg):
    journal = MergeJournal(tmp_path / "j.sqlite")
    await journal.init()
    corpus = InMemoryCorpus()
    llm = FakeLLMClient(canned=_canned(), default_model=_HAIKU)
    embed = FakeEmbeddingClient(model=cfg.embed_model_id)
    budget = Budget(cap_usd=cfg.max_cost_usd_per_ingest)
    classifier = FakeSlopClassifier(default_score=0.0)
    root = tmp_path / "post_mortems"

    # Run #1.
    r1 = await ingest(
        sources=[_OneShotSource()],
        enrichers=[],
        journal=journal,
        corpus=corpus,
        llm=llm,
        embed_client=embed,
        budget=budget,
        slop_classifier=classifier,
        config=cfg,
        post_mortems_root=root,
        sparse_encoder=_stub_sparse,
    )
    assert r1.processed == 1
    n_points_after_1 = len(corpus.points)
    assert n_points_after_1 >= 1

    # Run #2 — same corpus + journal + classifier. Skip-key should short-circuit.
    r2 = await ingest(
        sources=[_OneShotSource()],
        enrichers=[],
        journal=journal,
        corpus=corpus,
        llm=llm,
        embed_client=embed,
        budget=budget,
        slop_classifier=classifier,
        config=cfg,
        post_mortems_root=root,
        sparse_encoder=_stub_sparse,
    )
    assert r2.skipped >= 1
    # No new points appended.
    assert len(corpus.points) == n_points_after_1


async def test_ingest_force_bypasses_skip_key(tmp_path, cfg):
    journal = MergeJournal(tmp_path / "j.sqlite")
    await journal.init()
    corpus = InMemoryCorpus()
    llm = FakeLLMClient(canned=_canned(), default_model=_HAIKU)
    embed = FakeEmbeddingClient(model=cfg.embed_model_id)
    budget = Budget(cap_usd=cfg.max_cost_usd_per_ingest)
    classifier = FakeSlopClassifier(default_score=0.0)
    root = tmp_path / "post_mortems"

    await ingest(
        sources=[_OneShotSource()],
        enrichers=[],
        journal=journal,
        corpus=corpus,
        llm=llm,
        embed_client=embed,
        budget=budget,
        slop_classifier=classifier,
        config=cfg,
        post_mortems_root=root,
        sparse_encoder=_stub_sparse,
    )
    n1 = len(corpus.points)

    # With force=True, ingest re-processes even though skip_key matches.
    r2 = await ingest(
        sources=[_OneShotSource()],
        enrichers=[],
        journal=journal,
        corpus=corpus,
        llm=llm,
        embed_client=embed,
        budget=budget,
        slop_classifier=classifier,
        config=cfg,
        post_mortems_root=root,
        force=True,
        sparse_encoder=_stub_sparse,
    )
    # With force, processed is non-zero (re-processed).
    assert r2.processed >= 1
    assert r2.skipped == 0
    # delete-then-re-upsert: same canonical_id produces the same chunk count.
    # No duplicate orphans should remain.
    assert len(corpus.points) == n1
