from __future__ import annotations

from slopmortem.corpus import MergeJournal


async def test_quarantine_write_and_fetch(tmp_path):
    j = MergeJournal(tmp_path / "j.sqlite")
    await j.init()
    await j.write_quarantine(
        content_sha256="a" * 64,
        source="hn",
        source_id="2",
        reason="slop_score_high",
        slop_score=0.9,
    )
    rows = await j.fetch_quarantined()
    assert len(rows) == 1
    assert rows[0]["content_sha256"] == "a" * 64
    assert rows[0]["reason"] == "slop_score_high"
    assert rows[0]["slop_score"] == 0.9


async def test_quarantine_journal_no_canonical_id(tmp_path):
    j = MergeJournal(tmp_path / "j.sqlite")
    await j.init()
    await j.write_quarantine(
        content_sha256="b" * 64,
        source="hn",
        source_id="3",
        reason="slop_score_high",
        slop_score=0.95,
    )
    rows = await j.fetch_quarantined()
    assert len(rows) == 1
    # Blocker B4: no merge_state column on quarantine rows; no canonical_id either.
    assert "merge_state" not in rows[0]
    assert "canonical_id" not in rows[0]


async def test_quarantine_idempotent(tmp_path):
    j = MergeJournal(tmp_path / "j.sqlite")
    await j.init()
    sha = "c" * 64
    await j.write_quarantine(
        content_sha256=sha, source="hn", source_id="4", reason="r", slop_score=0.8
    )
    await j.write_quarantine(
        content_sha256=sha, source="hn", source_id="4", reason="r", slop_score=0.8
    )
    rows = await j.fetch_quarantined()
    assert len(rows) == 1
