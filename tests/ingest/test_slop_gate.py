"""Slop-gate quarantine routing.

Cassettes do not cover this path because no LLM call is made on quarantined
entries — they get no facet, no summary, no embed, no upsert. Pure unit tests
against the slop-gate module.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from slopmortem.corpus import MergeJournal
from slopmortem.ingest import FakeSlopClassifier, InMemoryCorpus
from slopmortem.ingest._slop_gate import _PRE_VETTED_SOURCES, _quarantine, classify_one
from slopmortem.models import RawEntry

if TYPE_CHECKING:
    from pathlib import Path


def _entry(*, source: str = "hn", source_id: str = "story-42") -> RawEntry:
    return RawEntry(
        source=source,
        source_id=source_id,
        url="https://example.com/post",
        raw_html=None,
        markdown_text="A long-winded LLM-flavored ramble " * 30,
        fetched_at=datetime(2026, 4, 30, tzinfo=UTC),
    )


async def test_above_threshold_entry_routes_to_quarantine_no_qdrant_no_journal(
    tmp_path: Path,
) -> None:
    """slop_score > threshold writes a quarantine row + file and skips Qdrant/journal."""
    journal = MergeJournal(tmp_path / "journal.sqlite")
    await journal.init()
    corpus = InMemoryCorpus()
    classifier = FakeSlopClassifier(default_score=0.99)
    threshold = 0.7
    post_mortems_root = tmp_path / "post_mortems"
    entry = _entry()
    body = entry.markdown_text or ""

    score = await classify_one(
        entry=entry,
        body=body,
        slop_classifier=classifier,
        pre_vetted_sources=_PRE_VETTED_SOURCES,
        on_error=lambda _exc: None,
    )
    assert score == 0.99
    assert score > threshold

    await _quarantine(
        journal=journal,
        entry=entry,
        body=body,
        slop_score=score,
        post_mortems_root=post_mortems_root,
    )

    quarantined = await journal.fetch_quarantined()
    assert len(quarantined) == 1
    assert quarantined[0]["source"] == entry.source
    assert quarantined[0]["source_id"] == entry.source_id
    assert quarantined[0]["reason"] == "slop_classifier"

    quarantine_dir = post_mortems_root / "quarantine"
    assert quarantine_dir.exists(), "quarantine path must have been created"
    files = list(quarantine_dir.glob("*.md"))
    assert len(files) == 1, "exactly one quarantine markdown file must land on disk"

    # No Qdrant point, no merge journal pending row.
    assert corpus.points == []
    pending = await journal.fetch_pending()
    assert pending == []


async def test_pre_vetted_source_bypasses_classifier_even_at_high_score(
    tmp_path: Path,
) -> None:
    """Entries from pre-vetted sources skip the classifier and never quarantine."""
    journal = MergeJournal(tmp_path / "journal.sqlite")
    await journal.init()
    corpus = InMemoryCorpus()

    class _ExplodingClassifier:
        async def score(self, text: str) -> float:
            del text  # parameter name must match SlopClassifier protocol
            msg = "classifier must not be called for pre-vetted sources"
            raise AssertionError(msg)

    # "curated" is the canonical pre-vetted source per _PRE_VETTED_SOURCES.
    assert "curated" in _PRE_VETTED_SOURCES
    entry = _entry(source="curated", source_id="acme-1")
    body = entry.markdown_text or ""

    score = await classify_one(
        entry=entry,
        body=body,
        slop_classifier=_ExplodingClassifier(),
        pre_vetted_sources=_PRE_VETTED_SOURCES,
        on_error=lambda _exc: None,
    )
    assert score == 0.0, "pre-vetted bypass must return 0.0 without invoking the classifier"

    threshold = 0.7
    assert score <= threshold, "pre-vetted entry must not route to quarantine"

    # Sanity: nothing was quarantined and no Qdrant point exists.
    assert await journal.fetch_quarantined() == []
    assert corpus.points == []
