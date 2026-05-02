"""Tests for entity resolution: tier-1 / tier-2 / tier-3, recycled domains, parent suffix, flips."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from conftest import llm_canned_key
from slopmortem.corpus import MergeJournal, resolve_entity
from slopmortem.corpus.entity_resolution import ResolveResult
from slopmortem.llm import FakeEmbeddingClient, FakeLLMClient, FakeResponse, render_prompt
from slopmortem.models import MergeState, RawEntry
from slopmortem.tracing import SpanEvent


def _tier3_canned(
    *,
    sibling: str,
    name_new: str,
    section_head_new: str,
    text: str,
    model: str,
) -> dict[tuple[str, str, str], FakeResponse]:
    """Build a canned entry keyed on the tier3 tiebreaker prompt rendered with these args."""
    rendered = render_prompt(
        "tier3_tiebreaker",
        name_a=sibling,
        name_b=name_new,
        section_head_a="",
        section_head_b=section_head_new[:200],
    )
    return {
        llm_canned_key("tier3_tiebreaker", model=model, prompt=rendered): FakeResponse(text=text),
    }


def make_entry(
    *,
    url: str,
    source: str = "curated",
    source_id: str = "1",
    name: str = "Acme",
    text: str = "Acme was a startup that built widgets and ran out of money.",
) -> RawEntry:
    """Build a minimal RawEntry test fixture."""
    del name  # name is passed to resolve_entity directly, not embedded in RawEntry
    return RawEntry(
        source=source,
        source_id=source_id,
        url=url,
        raw_html=None,
        markdown_text=text,
        fetched_at=datetime(2026, 4, 28, tzinfo=UTC),
    )


@pytest_asyncio.fixture
async def journal(tmp_path):
    j = MergeJournal(tmp_path / "j.sqlite")
    await j.init()
    return j


@pytest.fixture
def embed_client():
    return FakeEmbeddingClient(model="text-embedding-3-small")


async def _seed_complete(journal, *, canonical_id: str, source: str, source_id: str) -> None:
    """Seed the journal with a complete row for this (canonical_id, source, source_id)."""
    await journal.upsert_pending(canonical_id=canonical_id, source=source, source_id=source_id)
    await journal.mark_complete(
        canonical_id=canonical_id,
        source=source,
        source_id=source_id,
        skip_key="k1",
        merged_at="2026-04-28T00:00:00Z",
    )


async def test_tier1_platform_domains_dont_collapse(journal, embed_client):
    e1 = make_entry(
        url="https://username.medium.com/post-mortem-acme",
        source_id="1",
        name="Acme",
    )
    e2 = make_entry(
        url="https://otheruser.medium.com/post-mortem-bravo",
        source_id="2",
        name="Bravo",
    )
    r1 = await resolve_entity(
        e1, journal=journal, embed_client=embed_client, name="Acme", sector="saas"
    )
    r2 = await resolve_entity(
        e2, journal=journal, embed_client=embed_client, name="Bravo", sector="fintech"
    )
    assert r1.canonical_id != r2.canonical_id
    assert r1.canonical_id != "medium.com"
    assert r2.canonical_id != "medium.com"


async def test_tier1_non_platform_domain_uses_registrable(journal, embed_client):
    entry = make_entry(
        url="https://www.acme.com/post-mortem", source="curated", source_id="42", name="Acme"
    )
    r = await resolve_entity(
        entry, journal=journal, embed_client=embed_client, name="Acme", sector="saas"
    )
    assert r.canonical_id == "acme.com"
    assert r.action == "create"


async def test_recycled_domain_demotes_to_tier2(journal, embed_client):
    e1 = make_entry(url="https://www.acme.com/old", source_id="1")
    r1 = await resolve_entity(
        e1,
        journal=journal,
        embed_client=embed_client,
        name="Acme",
        sector="saas",
        founding_year=1998,
    )
    await _seed_complete(
        journal, canonical_id=r1.canonical_id, source=e1.source, source_id=e1.source_id
    )
    e2 = make_entry(url="https://www.acme.com/new", source_id="2")
    r2 = await resolve_entity(
        e2,
        journal=journal,
        embed_client=embed_client,
        name="AcmeReborn",
        sector="saas",
        founding_year=2018,
    )
    assert r2.canonical_id != r1.canonical_id


async def test_recycled_domain_within_decade_does_not_demote(journal, embed_client):
    e1 = make_entry(url="https://www.acme.com/p1", source_id="1")
    r1 = await resolve_entity(
        e1,
        journal=journal,
        embed_client=embed_client,
        name="Acme",
        sector="saas",
        founding_year=2010,
    )
    await _seed_complete(
        journal, canonical_id=r1.canonical_id, source=e1.source, source_id=e1.source_id
    )
    e2 = make_entry(url="https://www.acme.com/p2", source_id="2")
    r2 = await resolve_entity(
        e2,
        journal=journal,
        embed_client=embed_client,
        name="Acme",
        sector="saas",
        founding_year=2015,
    )
    assert r2.canonical_id == r1.canonical_id


async def test_parent_subsidiary_suffix_demotes(journal, embed_client):
    e1 = make_entry(url="https://www.acme.com/p1", source_id="1")
    r1 = await resolve_entity(
        e1, journal=journal, embed_client=embed_client, name="Acme Holdings", sector="saas"
    )
    await _seed_complete(
        journal, canonical_id=r1.canonical_id, source=e1.source, source_id=e1.source_id
    )
    e2 = make_entry(url="https://www.acme.com/p2", source_id="2")
    r2 = await resolve_entity(
        e2, journal=journal, embed_client=embed_client, name="Acme Corp", sector="saas"
    )
    assert r2.canonical_id != r1.canonical_id
    assert SpanEvent.PARENT_SUBSIDIARY_SUSPECTED.value in r2.span_events


async def test_same_name_no_suffix_delta_does_not_demote(journal, embed_client):
    e1 = make_entry(url="https://www.acme.com/p1", source_id="1")
    r1 = await resolve_entity(
        e1, journal=journal, embed_client=embed_client, name="Acme", sector="saas"
    )
    await _seed_complete(
        journal, canonical_id=r1.canonical_id, source=e1.source, source_id=e1.source_id
    )
    e2 = make_entry(url="https://www.acme.com/p2", source_id="2")
    r2 = await resolve_entity(
        e2, journal=journal, embed_client=embed_client, name="Acme", sector="saas"
    )
    assert r2.canonical_id == r1.canonical_id


async def test_tier3_high_similarity_auto_merges(journal, embed_client):
    e1 = make_entry(
        url="https://a.medium.com/p1",
        source_id="1",
        text="Acme was a SaaS company that built widgets.",
    )
    r1 = await resolve_entity(
        e1, journal=journal, embed_client=embed_client, name="Acme", sector="saas"
    )
    await _seed_complete(
        journal, canonical_id=r1.canonical_id, source=e1.source, source_id=e1.source_id
    )
    e2 = make_entry(
        url="https://b.substack.com/p1",
        source_id="2",
        text="Acme was a SaaS company that built widgets.",
    )
    r2 = await resolve_entity(
        e2, journal=journal, embed_client=embed_client, name="Acme", sector="saas"
    )
    # Tier-2 (name + sector) collapses these; both are forced off tier-1 by the platform blocklist.
    assert r2.canonical_id == r1.canonical_id


async def test_tier3_band_invokes_haiku_tiebreaker(journal, embed_client):
    e1 = make_entry(url="https://x.medium.com/p1", source_id="1", text="Acme widgets.")
    r1 = await resolve_entity(
        e1, journal=journal, embed_client=embed_client, name="Acme", sector="saas"
    )
    await _seed_complete(
        journal, canonical_id=r1.canonical_id, source=e1.source, source_id=e1.source_id
    )
    haiku_model = "anthropic/claude-haiku-4.5"
    e2 = make_entry(
        url="https://y.medium.com/p2", source_id="2", text="AcmeAI widgets re-imagined."
    )
    fake_llm = FakeLLMClient(
        canned=_tier3_canned(
            sibling=r1.canonical_id,
            name_new="AcmeAI",
            section_head_new=e2.markdown_text or "",
            text='{"decision": "same", "rationale": "Same product, same domain area."}',
            model=haiku_model,
        ),
        default_model=haiku_model,
    )
    r2 = await resolve_entity(
        e2,
        journal=journal,
        embed_client=embed_client,
        llm_client=fake_llm,
        haiku_model_id=haiku_model,
        name="AcmeAI",
        sector="saas",
        force_similarity=0.75,
    )
    assert r2.canonical_id == r1.canonical_id
    assert len(fake_llm.calls) == 1


async def test_tier3_below_band_creates_new(journal, embed_client):
    e1 = make_entry(url="https://x.medium.com/p1", source_id="1", text="Acme widgets.")
    r1 = await resolve_entity(
        e1, journal=journal, embed_client=embed_client, name="Acme", sector="saas"
    )
    await _seed_complete(
        journal, canonical_id=r1.canonical_id, source=e1.source, source_id=e1.source_id
    )
    e2 = make_entry(
        url="https://y.medium.com/p2",
        source_id="2",
        text="A bakery in Portugal that closed last year.",
    )
    r2 = await resolve_entity(
        e2,
        journal=journal,
        embed_client=embed_client,
        name="WildlyDifferent",
        sector="food",
        force_similarity=0.40,
    )
    assert r2.canonical_id != r1.canonical_id


async def test_tier3_decision_is_cached(journal, embed_client):
    e1 = make_entry(url="https://x.medium.com/p1", source_id="1")
    r1 = await resolve_entity(
        e1, journal=journal, embed_client=embed_client, name="Acme", sector="saas"
    )
    await _seed_complete(
        journal, canonical_id=r1.canonical_id, source=e1.source, source_id=e1.source_id
    )
    haiku_model = "anthropic/claude-haiku-4.5"
    e2 = make_entry(url="https://y.medium.com/p2", source_id="2")
    fake_llm = FakeLLMClient(
        canned=_tier3_canned(
            sibling=r1.canonical_id,
            name_new="AcmeAI",
            section_head_new=e2.markdown_text or "",
            text='{"decision": "same", "rationale": "yep"}',
            model=haiku_model,
        ),
        default_model=haiku_model,
    )
    _ = await resolve_entity(
        e2,
        journal=journal,
        embed_client=embed_client,
        llm_client=fake_llm,
        haiku_model_id=haiku_model,
        name="AcmeAI",
        sector="saas",
        force_similarity=0.75,
    )
    e3 = make_entry(url="https://z.medium.com/p3", source_id="3")
    _ = await resolve_entity(
        e3,
        journal=journal,
        embed_client=embed_client,
        llm_client=fake_llm,
        haiku_model_id=haiku_model,
        name="AcmeAI",
        sector="saas",
        force_similarity=0.75,
    )
    assert len(fake_llm.calls) == 1


async def test_resolver_flip_detected(journal, embed_client):
    await _seed_complete(journal, canonical_id="old.com", source="curated", source_id="1")
    e2 = make_entry(url="https://www.new-canonical.com/p", source_id="1")
    result = await resolve_entity(
        e2, journal=journal, embed_client=embed_client, name="NewCanon", sector="saas"
    )
    assert result.action == "resolver_flipped"
    assert result.prior_canonical_id == "old.com"
    rows = await journal.fetch_by_key("new-canonical.com", "curated", "1")
    assert len(rows) == 1
    assert rows[0]["merge_state"] == MergeState.RESOLVER_FLIPPED.value
    assert SpanEvent.RESOLVER_FLIP_DETECTED.value in result.span_events


async def test_resolve_returns_typed_result(journal, embed_client):
    e = make_entry(url="https://www.foo.com/p", source_id="9")
    r = await resolve_entity(
        e, journal=journal, embed_client=embed_client, name="Foo", sector="saas"
    )
    assert isinstance(r, ResolveResult)
    assert r.canonical_id
    assert r.action in {"create", "merge", "resolver_flipped", "alias_blocked"}
