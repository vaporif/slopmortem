"""Alias-graph atomicity tests: alias_blocked rows + edges land in one transaction."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from slopmortem.corpus import MergeJournal, resolve_entity
from slopmortem.llm import FakeEmbeddingClient
from slopmortem.models import AliasEdge, MergeState, RawEntry


@pytest_asyncio.fixture
async def journal(tmp_path):
    j = MergeJournal(tmp_path / "j.sqlite")
    await j.init()
    return j


@pytest.fixture
def embed_client():
    return FakeEmbeddingClient(model="text-embedding-3-small")


def make_entry(*, url: str, source_id: str, text: str = "body") -> RawEntry:
    return RawEntry(
        source="curated",
        source_id=source_id,
        url=url,
        markdown_text=text,
        fetched_at=datetime(2026, 4, 28, tzinfo=UTC),
    )


async def test_alias_blocked_atomic_no_pending_residue(journal, embed_client):
    """Trigger alias case via alias_hint; verify journal lands once at alias_blocked."""
    await journal.upsert_pending(canonical_id="acme.com", source="curated", source_id="0")
    await journal.mark_complete(
        canonical_id="acme.com",
        source="curated",
        source_id="0",
        skip_key="k1",
        merged_at="2026-04-28T00:00:00Z",
    )
    # New entry on the same domain claims it became a new entity.
    entry = make_entry(url="https://www.acme.com/founder-blog", source_id="1")
    alias_hint = AliasEdge(
        canonical_id="acme.com",
        alias_kind="rebranded_to",
        target_canonical_id="acme-ai.com",
        evidence_source_id="curated:1",
        confidence=0.92,
    )
    # Capture every state-transition write: monkeypatch upsert_pending to record.
    states: list[str] = []
    original_pending = journal.upsert_pending
    original_alias = journal.upsert_alias_blocked

    async def trace_pending(**kwargs):
        states.append("pending")
        await original_pending(**kwargs)

    async def trace_alias(**kwargs):
        states.append("alias_blocked")
        await original_alias(**kwargs)

    journal.upsert_pending = trace_pending
    journal.upsert_alias_blocked = trace_alias

    result = await resolve_entity(
        entry,
        journal=journal,
        embed_client=embed_client,
        name="AcmeAI",
        sector="saas",
        alias_hint=alias_hint,
    )
    assert result.action == "alias_blocked"
    assert states == ["alias_blocked"]
    edges = await journal.fetch_aliases("acme.com")
    assert len(edges) == 1
    assert edges[0].target_canonical_id == "acme-ai.com"
    rows = await journal.fetch_by_key("acme.com", "curated", "1")
    assert len(rows) == 1
    assert rows[0]["merge_state"] == MergeState.ALIAS_BLOCKED.value


async def test_alias_blocked_crash_recovery(journal, embed_client, monkeypatch):
    """Simulate failure mid-transaction: both alias edge and journal row absent (ROLLBACK)."""
    await journal.upsert_pending(canonical_id="acme.com", source="curated", source_id="0")
    await journal.mark_complete(
        canonical_id="acme.com",
        source="curated",
        source_id="0",
        skip_key="k1",
        merged_at="2026-04-28T00:00:00Z",
    )
    entry = make_entry(url="https://www.acme.com/founder-blog", source_id="1")
    alias_hint = AliasEdge(
        canonical_id="acme.com",
        alias_kind="rebranded_to",
        target_canonical_id="acme-ai.com",
        evidence_source_id="curated:1",
        confidence=0.92,
    )

    boom = RuntimeError("simulated crash inside transaction")

    def crashing_upsert_alias_blocked(*_args, **_kwargs):
        raise boom

    monkeypatch.setattr(journal, "_upsert_alias_blocked_sync", crashing_upsert_alias_blocked)

    with pytest.raises(RuntimeError, match="simulated crash"):
        await resolve_entity(
            entry,
            journal=journal,
            embed_client=embed_client,
            name="AcmeAI",
            sector="saas",
            alias_hint=alias_hint,
        )

    # Neither the alias edge nor the journal row should exist.
    edges = await journal.fetch_aliases("acme.com")
    assert edges == []
    rows = await journal.fetch_by_key("acme.com", "curated", "1")
    assert rows == []


async def test_alias_no_hint_does_not_write_alias_row(journal, embed_client):
    entry = make_entry(url="https://www.foo.com/p", source_id="1")
    result = await resolve_entity(
        entry, journal=journal, embed_client=embed_client, name="Foo", sector="saas"
    )
    assert result.action == "create"
    edges = await journal.fetch_aliases("foo.com")
    assert edges == []
