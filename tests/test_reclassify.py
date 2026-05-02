"""reclassify_quarantined: re-score quarantined docs; route survivors to raw/."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from slopmortem.corpus import MergeJournal, reclassify_quarantined

if TYPE_CHECKING:
    from pathlib import Path


async def test_declassifies_doc_below_threshold(tmp_path: Path) -> None:
    """Quarantined doc that now scores below threshold moves to raw/ and row is dropped."""
    db = tmp_path / "journal.sqlite"
    quarantine_root = tmp_path / "post_mortems" / "quarantine"
    raw_root = tmp_path / "post_mortems" / "raw"
    quarantine_root.mkdir(parents=True)
    raw_root.mkdir(parents=True)

    journal = MergeJournal(db)
    await journal.init()

    sha = "a" * 64
    quarantine_md = quarantine_root / f"{sha}.md"
    quarantine_md.write_text("legitimate post-mortem body", encoding="utf-8")

    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO quarantine_journal VALUES (?, ?, ?, ?, ?, ?)",
        (sha, "hn_algolia", "story-123", "slop_score>0.7", 0.85, "2026-04-29T00:00:00Z"),
    )
    conn.commit()
    conn.close()

    fake_classifier = AsyncMock()
    fake_classifier.score = AsyncMock(return_value=0.4)

    report = await reclassify_quarantined(
        journal=journal,
        slop_classifier=fake_classifier,
        post_mortems_root=tmp_path / "post_mortems",
        slop_threshold=0.7,
    )

    assert report.total == 1
    assert report.declassified == 1
    assert report.still_slop == 0
    assert report.errors == 0
    # Quarantine row is gone.
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT * FROM quarantine_journal").fetchall()
    conn.close()
    assert len(rows) == 0
    # File moved out of quarantine into raw/<source>/<text_id>.md.
    assert not quarantine_md.exists()
    raw_target = raw_root / "hn_algolia" / f"{sha[:16]}.md"
    assert raw_target.exists()
    assert raw_target.read_text(encoding="utf-8") == "legitimate post-mortem body"


async def test_keeps_doc_above_threshold(tmp_path: Path) -> None:
    """Quarantined doc that still scores at-or-above threshold stays in quarantine."""
    db = tmp_path / "journal.sqlite"
    journal = MergeJournal(db)
    await journal.init()
    quarantine_root = tmp_path / "post_mortems" / "quarantine"
    quarantine_root.mkdir(parents=True)

    sha = "b" * 64
    qpath = quarantine_root / f"{sha}.md"
    qpath.write_text("slop content", encoding="utf-8")
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO quarantine_journal VALUES (?, ?, ?, ?, ?, ?)",
        (sha, "hn_algolia", "story-456", "slop_score>0.7", 0.95, "2026-04-29T00:00:00Z"),
    )
    conn.commit()
    conn.close()

    fake_classifier = AsyncMock()
    fake_classifier.score = AsyncMock(return_value=0.85)  # still above 0.7

    report = await reclassify_quarantined(
        journal=journal,
        slop_classifier=fake_classifier,
        post_mortems_root=tmp_path / "post_mortems",
        slop_threshold=0.7,
    )

    assert report.total == 1
    assert report.declassified == 0
    assert report.still_slop == 1
    assert report.errors == 0
    # Quarantine row and file still present.
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT * FROM quarantine_journal").fetchall()
    conn.close()
    assert len(rows) == 1
    assert qpath.exists()


async def test_handles_missing_quarantine_file(tmp_path: Path) -> None:
    """A quarantine_journal row whose markdown file is missing increments errors and continues."""
    db = tmp_path / "journal.sqlite"
    journal = MergeJournal(db)
    await journal.init()

    sha = "c" * 64
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO quarantine_journal VALUES (?, ?, ?, ?, ?, ?)",
        (sha, "hn_algolia", "story-789", "slop", 0.9, "2026-04-29T00:00:00Z"),
    )
    conn.commit()
    conn.close()

    fake_classifier = AsyncMock()
    fake_classifier.score = AsyncMock(return_value=0.0)

    report = await reclassify_quarantined(
        journal=journal,
        slop_classifier=fake_classifier,
        post_mortems_root=tmp_path / "post_mortems",
        slop_threshold=0.7,
    )

    assert report.total == 1
    assert report.errors == 1
    assert report.declassified == 0
    assert report.still_slop == 0
    # Classifier was never invoked because the file did not exist.
    fake_classifier.score.assert_not_awaited()
