"""--list-review reads the pending_review table and prints to stdout."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from slopmortem.cli import app
from slopmortem.corpus.merge import MergeJournal
from slopmortem.models import PendingReviewRow

if TYPE_CHECKING:
    from pathlib import Path


def test_pending_review_row_round_trips() -> None:
    row = PendingReviewRow(
        pair_key="acme:beta",
        similarity_score=0.78,
        haiku_decision="merge",
        haiku_rationale="same product, parent rebrand",
        raw_section_heads="acme=…|beta=…",
    )
    assert row.pair_key == "acme:beta"
    assert row.similarity_score == 0.78
    assert row.haiku_decision == "merge"


@pytest.mark.asyncio
async def test_list_pending_review_returns_rows(tmp_path: Path) -> None:
    """Insert two pending_review rows directly, assert the reader returns them."""
    db = tmp_path / "journal.sqlite"
    journal = MergeJournal(db)
    await journal.init()

    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO pending_review VALUES (?, ?, ?, ?, ?)",
        ("acme:beta", 0.78, "merge", "same product", "acme=…|beta=…"),
    )
    conn.execute(
        "INSERT INTO pending_review VALUES (?, ?, ?, ?, ?)",
        ("foo:bar", 0.83, "no_merge", "different segments", "foo=…|bar=…"),
    )
    conn.commit()
    conn.close()

    rows = await journal.list_pending_review()
    assert len(rows) == 2
    keys = {r.pair_key for r in rows}
    assert keys == {"acme:beta", "foo:bar"}


def test_cli_list_review_prints_queue(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--list-review queries the journal and prints rows."""
    fake_rows = [
        PendingReviewRow(
            pair_key="acme:beta",
            similarity_score=0.78,
            haiku_decision="merge",
            haiku_rationale="same product",
            raw_section_heads="acme=…|beta=…",
        )
    ]

    fake_journal = MagicMock()

    async def _afake() -> list[PendingReviewRow]:
        return fake_rows

    fake_journal.list_pending_review = _afake

    def _fake_deps(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        return (
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            fake_journal,
            MagicMock(),
        )

    monkeypatch.setattr("slopmortem.cli._build_ingest_deps", _fake_deps)

    runner = CliRunner()
    result = runner.invoke(app, ["ingest", "--list-review", "--post-mortems-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "acme:beta" in result.output
    assert "0.78" in result.output
