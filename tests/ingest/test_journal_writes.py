"""Crash-recovery tests for the journal write sequence.

The invariant from CLAUDE.md: mark_complete fires only after both Qdrant and
disk writes succeed. A crash between any pair of steps must leave the journal
row in a recoverable state and never produce an orphan mark_complete.
"""

from __future__ import annotations

import importlib
import typing
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from slopmortem.config import Config
from slopmortem.corpus import MergeJournal
from slopmortem.ingest import InMemoryCorpus
from slopmortem.ingest._fan_out import _FanoutResult
from slopmortem.ingest._journal_writes import _process_entry
from slopmortem.llm import FakeEmbeddingClient, FakeLLMClient
from slopmortem.models import Facets, RawEntry

if TYPE_CHECKING:
    from pathlib import Path


class _CrashAt(Exception):  # noqa: N818 - test marker, not an error class
    """Raised inside test doubles to abort write sequencing at a precise boundary."""


def _stub_sparse(_text: str) -> dict[int, float]:
    return {0: 1.0}


def _entry() -> RawEntry:
    return RawEntry(
        source="curated",
        source_id="acme-1",
        url="https://acme.com",
        raw_html=None,
        markdown_text="Acme was a startup that sold widgets and ran out of money in 2021. " * 30,
        fetched_at=datetime(2026, 4, 28, tzinfo=UTC),
    )


def _facets() -> Facets:
    return Facets(
        sector="retail_ecommerce",
        business_model="b2b_marketplace",
        customer_type="smb",
        geography="us",
        monetization="transaction_fee",
        founding_year=2018,
        failure_year=2021,
    )


def _fan_result() -> _FanoutResult:
    return _FanoutResult(
        facets=_facets(),
        summary="Acme summary.",
        cache_read=0,
        cache_creation=0,
    )


def _cfg() -> Config:
    return Config(max_cost_usd_per_ingest=100.0, ingest_concurrency=5)


# Façade re-exports shadow submodules, so monkeypatch must target the binding
# inside the module that owns _process_entry, not slopmortem.corpus.
_JOURNAL_WRITES_MOD_NAME = "slopmortem.ingest._journal_writes"


def _patch_target_module():
    return importlib.import_module(_JOURNAL_WRITES_MOD_NAME)


class _CrashOnUpsertCorpus(InMemoryCorpus):
    """Crash inside upsert_chunk; targets the (canonical → Qdrant) boundary."""

    @typing.override
    async def upsert_chunk(self, point: object) -> None:
        msg = "crash inside Qdrant upsert"
        raise _CrashAt(msg)


class _CrashOnDeleteCorpus(InMemoryCorpus):
    """Crash inside delete_chunks_for_canonical on a re-merge.

    _process_entry already wraps this call in try/except and converts the
    failure into ProcessOutcome.FAILED, so tests assert on the return value
    rather than on a raised exception.
    """

    @typing.override
    async def delete_chunks_for_canonical(self, canonical_id: str) -> None:
        msg = f"crash inside delete_chunks_for_canonical({canonical_id!r})"
        raise _CrashAt(msg)


async def _journal(tmp_path: Path) -> MergeJournal:
    j = MergeJournal(tmp_path / "journal.sqlite")
    await j.init()
    return j


async def _run_process_entry(
    *,
    journal: MergeJournal,
    corpus: InMemoryCorpus,
    config: Config,
    post_mortems_root: Path,
):
    return await _process_entry(
        _entry(),
        body=_entry().markdown_text or "",
        fan=_fan_result(),
        journal=journal,
        corpus=corpus,
        embed_client=FakeEmbeddingClient(model=config.embed_model_id),
        llm=FakeLLMClient(canned={}, default_model=config.model_facet),
        config=config,
        post_mortems_root=post_mortems_root,
        slop_score=0.0,
        force=False,
        span_events=[],
        sparse_encoder=_stub_sparse,
    )


async def test_crash_between_upsert_pending_and_write_raw_leaves_pending_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Crashing inside write_raw_atomic must leave the row pending and no raw file."""
    journal = await _journal(tmp_path)
    corpus = InMemoryCorpus()
    config = _cfg()
    post_mortems_root = tmp_path / "post_mortems"

    async def _explode(*_args: object, **_kwargs: object) -> None:
        msg = "crash inside write_raw_atomic"
        raise _CrashAt(msg)

    monkeypatch.setattr(_patch_target_module(), "write_raw_atomic", _explode)

    with pytest.raises(_CrashAt):
        await _run_process_entry(
            journal=journal,
            corpus=corpus,
            config=config,
            post_mortems_root=post_mortems_root,
        )

    pending = await journal.fetch_pending()
    assert len(pending) == 1, "upsert_pending must have written exactly one pending row"
    assert pending[0]["merge_state"] == "pending"
    # No raw file landed on disk; no Qdrant point.
    assert not (post_mortems_root / "raw").exists()
    assert corpus.points == []


async def test_crash_between_write_raw_and_write_canonical_leaves_recoverable_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Crashing inside write_canonical_atomic leaves raw on disk and the row pending."""
    journal = await _journal(tmp_path)
    corpus = InMemoryCorpus()
    config = _cfg()
    post_mortems_root = tmp_path / "post_mortems"

    async def _explode(*_args: object, **_kwargs: object) -> None:
        msg = "crash inside write_canonical_atomic"
        raise _CrashAt(msg)

    monkeypatch.setattr(_patch_target_module(), "write_canonical_atomic", _explode)

    with pytest.raises(_CrashAt):
        await _run_process_entry(
            journal=journal,
            corpus=corpus,
            config=config,
            post_mortems_root=post_mortems_root,
        )

    pending = await journal.fetch_pending()
    assert len(pending) == 1
    assert pending[0]["merge_state"] == "pending"
    # write_raw_atomic ran — the raw tree should exist; canonical must not.
    assert (post_mortems_root / "raw").exists()
    assert not (post_mortems_root / "canonical").exists()
    assert corpus.points == []


async def test_crash_between_write_canonical_and_qdrant_upsert_leaves_recoverable_state(
    tmp_path: Path,
) -> None:
    """Crashing during Qdrant upsert (after canonical disk write) keeps the row pending.

    First pass writes the entry through end-to-end so the second pass exercises
    the existing-row branch where delete_chunks_for_canonical runs. We crash
    inside delete_chunks_for_canonical, which _process_entry catches and
    converts to ProcessOutcome.FAILED before any upsert layer is touched.
    """
    journal = await _journal(tmp_path)
    config = _cfg()
    post_mortems_root = tmp_path / "post_mortems"

    # Pass 1: clean run, leaves a complete row + canonical + chunks on disk.
    good_corpus = InMemoryCorpus()
    outcome_1 = await _run_process_entry(
        journal=journal,
        corpus=good_corpus,
        config=config,
        post_mortems_root=post_mortems_root,
    )
    assert outcome_1.value == "processed"
    assert good_corpus.points  # sanity: pass 1 actually wrote points

    # Pass 2: same entry, but with --force so the skip-key shortcut doesn't
    # take. We crash inside delete_chunks_for_canonical to simulate a
    # write-canonical-success / Qdrant-upsert-fail boundary. _process_entry
    # converts this to FAILED.
    crashing_corpus = _CrashOnDeleteCorpus()
    outcome_2 = await _process_entry(
        _entry(),
        body=_entry().markdown_text or "",
        fan=_fan_result(),
        journal=journal,
        corpus=crashing_corpus,
        embed_client=FakeEmbeddingClient(model=config.embed_model_id),
        llm=FakeLLMClient(canned={}, default_model=config.model_facet),
        config=config,
        post_mortems_root=post_mortems_root,
        slop_score=0.0,
        force=True,
        span_events=[],
        sparse_encoder=_stub_sparse,
    )
    assert outcome_2.value == "failed"
    # The journal row from pass 1 was complete; upsert_pending in pass 2
    # flips it back to pending, and mark_complete never re-fires.
    pending = await journal.fetch_pending()
    assert len(pending) == 1
    assert pending[0]["merge_state"] == "pending"
    # Crashing corpus never had upsert_chunk called.
    assert crashing_corpus.points == []


async def test_crash_between_qdrant_upsert_and_mark_complete_no_orphan_mark_complete(
    tmp_path: Path,
) -> None:
    """Crashing inside upsert_chunk must keep the row pending — no orphan mark_complete."""
    journal = await _journal(tmp_path)
    corpus = _CrashOnUpsertCorpus()
    config = _cfg()
    post_mortems_root = tmp_path / "post_mortems"

    with pytest.raises(_CrashAt):
        await _run_process_entry(
            journal=journal,
            corpus=corpus,
            config=config,
            post_mortems_root=post_mortems_root,
        )

    pending = await journal.fetch_pending()
    assert len(pending) == 1, "row must stay pending — mark_complete must NOT have fired"
    assert pending[0]["merge_state"] == "pending"
    assert pending[0].get("skip_key") in (None, "")
    # Disk side did write, but Qdrant upsert blew up before any point landed.
    assert corpus.points == []
