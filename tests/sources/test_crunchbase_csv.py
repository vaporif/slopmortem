"""Crunchbase CSV source: file-based ``RawEntry`` producer (no HTTP)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from slopmortem.corpus.sources import CrunchbaseCsvSource

if TYPE_CHECKING:
    from pathlib import Path

CSV_TEXT = (
    "uuid,name,homepage_url,short_description\n"
    "00000000-0000-0000-0000-000000000001,Acme Corp,https://acme.example,"
    "We made widgets and ran out of cash.\n"
    "00000000-0000-0000-0000-000000000002,Beta LLC,https://beta.example,"
    "Pivoted thrice and shut down.\n"
)


async def test_crunchbase_yields_entry_per_row(tmp_path: Path) -> None:
    csv_path = tmp_path / "crunchbase.csv"
    csv_path.write_text(CSV_TEXT, encoding="utf-8")

    src = CrunchbaseCsvSource(csv_path=csv_path)
    entries = [e async for e in src.fetch()]

    assert len(entries) == 2
    assert all(e.source == "crunchbase_csv" for e in entries)
    ids = {e.source_id for e in entries}
    assert "00000000-0000-0000-0000-000000000001" in ids
    assert entries[0].url == "https://acme.example"
    assert entries[0].markdown_text is not None
    assert "widgets" in entries[0].markdown_text


async def test_crunchbase_handles_missing_homepage(tmp_path: Path) -> None:
    text = "uuid,name,homepage_url,short_description\nabc,NoUrlCo,,A description without a URL.\n"
    csv_path = tmp_path / "crunchbase.csv"
    csv_path.write_text(text, encoding="utf-8")

    src = CrunchbaseCsvSource(csv_path=csv_path)
    entries = [e async for e in src.fetch()]
    assert len(entries) == 1
    assert entries[0].url is None
    assert entries[0].source_id == "abc"
