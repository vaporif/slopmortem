"""Per-entry isolation: enricher / fan-out failures don't abort the run.

Pins the "log and continue" contract from CLAUDE.md so it survives the
ingest() refactor that splits the orchestrator into phase functions.
"""

from __future__ import annotations

import json
import typing
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from conftest import llm_canned_key
from slopmortem.budget import Budget
from slopmortem.config import Config
from slopmortem.corpus import MergeJournal
from slopmortem.ingest import FakeSlopClassifier, InMemoryCorpus, ingest
from slopmortem.llm import FakeEmbeddingClient, FakeLLMClient, FakeResponse, render_prompt
from slopmortem.models import RawEntry
from slopmortem.tracing import SpanEvent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_HAIKU = "anthropic/claude-haiku-4.5"
_BODY = "Acme was a startup that sold widgets and ran out of money in 2021. " * 30


def _stub_sparse(_text: str) -> dict[int, float]:
    return {0: 1.0}


def _facets_json() -> str:
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
    facet_resp = FakeResponse(
        text=_facets_json(), cache_creation_tokens=1000, cache_read_tokens=5000
    )
    summary_resp = FakeResponse(
        text="Acme summary.", cache_creation_tokens=0, cache_read_tokens=5000
    )
    facet_prompt = render_prompt("facet_extract", description=_BODY)
    summarize_warm_prompt = render_prompt("summarize", body=_BODY[:1000], source_id="warm")
    summarize_fanout_prompt = render_prompt("summarize", body=_BODY, source_id="")
    return {
        llm_canned_key("facet_extract", model=_HAIKU, prompt=facet_prompt): facet_resp,
        llm_canned_key("summarize", model=_HAIKU, prompt=summarize_warm_prompt): summary_resp,
        llm_canned_key("summarize", model=_HAIKU, prompt=summarize_fanout_prompt): summary_resp,
    }


def _entry(source_id: str) -> RawEntry:
    return RawEntry(
        source="curated",
        source_id=source_id,
        url=f"https://acme-{source_id}.com",
        raw_html=None,
        markdown_text=_BODY,
        fetched_at=datetime(2026, 4, 28, tzinfo=UTC),
    )


class _ListSource:
    def __init__(self, entries: list[RawEntry]) -> None:
        self.entries = entries

    async def fetch(self) -> AsyncIterator[RawEntry]:
        for e in self.entries:
            yield e


class _RaisingEnricher:
    """Enricher that raises on entries whose source_id is in `bad_ids`."""

    def __init__(self, bad_ids: set[str]) -> None:
        self.bad_ids = bad_ids

    async def enrich(self, entry: RawEntry) -> RawEntry:
        if entry.source_id in self.bad_ids:
            msg = f"enricher exploded on {entry.source_id}"
            raise RuntimeError(msg)
        return entry


@pytest.fixture
def cfg() -> Config:
    return Config(max_cost_usd_per_ingest=100.0, ingest_concurrency=20)


async def test_enricher_failure_isolates_entry_keeps_run_alive(tmp_path, cfg):
    """An enricher exception on one entry counts as result.errors, siblings still process."""
    journal = MergeJournal(tmp_path / "j.sqlite")
    await journal.init()
    corpus = InMemoryCorpus()
    llm = FakeLLMClient(canned=_canned(), default_model=_HAIKU)
    embed = FakeEmbeddingClient(model=cfg.embed_model_id)
    budget = Budget(cap_usd=cfg.max_cost_usd_per_ingest)
    classifier = FakeSlopClassifier(default_score=0.0)

    entries = [_entry("good-1"), _entry("bad"), _entry("good-2")]
    sources = [_ListSource(entries)]
    enrichers = [_RaisingEnricher(bad_ids={"bad"})]

    result = await ingest(
        sources=sources,
        enrichers=enrichers,
        journal=journal,
        corpus=corpus,
        llm=llm,
        embed_client=embed,
        budget=budget,
        slop_classifier=classifier,
        config=cfg,
        post_mortems_root=tmp_path / "post_mortems",
        sparse_encoder=_stub_sparse,
    )

    assert result.seen == 3
    assert result.processed == 2
    assert result.errors == 1
    assert SpanEvent.INGEST_ENTRY_FAILED.value in result.span_events


class _FanoutFailingLLM(FakeLLMClient):
    """Raises ``RuntimeError`` whenever the rendered prompt contains a marker substring.

    We can't key on entry source_id (the LLM never sees it), so the test
    body uses different ``markdown_text`` per entry and the LLM matches on
    the body's own substring.
    """

    fail_marker: str = ""

    @typing.override
    async def complete(self, prompt, **kw):
        if self.fail_marker and self.fail_marker in prompt:
            msg = f"simulated facet/summarize failure on prompt containing {self.fail_marker!r}"
            raise RuntimeError(msg)
        return await super().complete(prompt, **kw)


def _entry_with_body(source_id: str, body: str) -> RawEntry:
    return RawEntry(
        source="curated",
        source_id=source_id,
        url=f"https://acme-{source_id}.com",
        raw_html=None,
        markdown_text=body,
        fetched_at=datetime(2026, 4, 28, tzinfo=UTC),
    )


def _canned_for_bodies(bodies: list[str]) -> dict[tuple[str, str, str], FakeResponse]:
    """Canned responses for a set of distinct bodies. Cache-warm uses bodies[0][:1000]."""
    facet_resp = FakeResponse(
        text=_facets_json(), cache_creation_tokens=1000, cache_read_tokens=5000
    )
    summary_resp = FakeResponse(
        text="summary text.", cache_creation_tokens=0, cache_read_tokens=5000
    )
    out: dict[tuple[str, str, str], FakeResponse] = {}
    # cache-warm uses the first entry's body[:1000]
    warm_prompt = render_prompt("summarize", body=bodies[0][:1000], source_id="warm")
    out[llm_canned_key("summarize", model=_HAIKU, prompt=warm_prompt)] = summary_resp
    for body in bodies:
        facet_prompt = render_prompt("facet_extract", description=body)
        summarize_prompt = render_prompt("summarize", body=body, source_id="")
        out[llm_canned_key("facet_extract", model=_HAIKU, prompt=facet_prompt)] = facet_resp
        out[llm_canned_key("summarize", model=_HAIKU, prompt=summarize_prompt)] = summary_resp
    return out


async def test_fanout_failure_isolates_entry_keeps_run_alive(tmp_path, cfg):
    """An LLM exception in fan-out for one entry counts as errors; siblings still process."""
    journal = MergeJournal(tmp_path / "j.sqlite")
    await journal.init()
    corpus = InMemoryCorpus()

    body_good_1 = "Good company alpha was a marketplace that ran out of money. " * 30
    body_bad = "MARKER_BAD this entry will trigger a fan-out exception. " * 30
    body_good_2 = "Good company beta was a SaaS that pivoted and shut down. " * 30
    bodies = [body_good_1, body_bad, body_good_2]

    canned = _canned_for_bodies(bodies)
    llm = _FanoutFailingLLM(canned=canned, default_model=_HAIKU)
    llm.fail_marker = "MARKER_BAD"

    embed = FakeEmbeddingClient(model=cfg.embed_model_id)
    budget = Budget(cap_usd=cfg.max_cost_usd_per_ingest)
    classifier = FakeSlopClassifier(default_score=0.0)

    entries = [
        _entry_with_body("good-1", body_good_1),
        _entry_with_body("bad", body_bad),
        _entry_with_body("good-2", body_good_2),
    ]
    sources = [_ListSource(entries)]

    result = await ingest(
        sources=sources,
        enrichers=[],
        journal=journal,
        corpus=corpus,
        llm=llm,
        embed_client=embed,
        budget=budget,
        slop_classifier=classifier,
        config=cfg,
        post_mortems_root=tmp_path / "post_mortems",
        sparse_encoder=_stub_sparse,
    )

    assert result.seen == 3
    assert result.processed == 2
    assert result.errors == 1
    assert SpanEvent.INGEST_ENTRY_FAILED.value in result.span_events
