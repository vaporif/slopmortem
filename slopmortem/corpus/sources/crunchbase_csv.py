"""Crunchbase CSV source. File-based ``RawEntry`` producer.

Crunchbase exports their organization dataset as CSV; v1 takes the path at
construction time (passed via ``--crunchbase-csv path`` at the CLI). No HTTP
happens here, so the throttle and robots checks don't apply. Mapping:

* ``source = "crunchbase_csv"``
* ``source_id = uuid`` if present, else falls back to the company name
* ``url = homepage_url`` (may be empty)
* ``markdown_text = short_description`` (may be empty)
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from slopmortem.models import RawEntry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

# Crunchbase export variants use different column names for the same field.
_ID_COLS = ("uuid", "id", "permalink", "cb_url")
_NAME_COLS = ("name", "company_name", "organization_name")
_URL_COLS = ("homepage_url", "homepage", "url", "website")
_DESC_COLS = ("short_description", "description", "long_description", "about")


def _first_present(row: dict[str, str], cols: tuple[str, ...]) -> str:
    for col in cols:
        val = row.get(col)
        if val:
            return val.strip()
    return ""


class CrunchbaseCsvSource:
    """[Source] Reads a Crunchbase organization CSV; one ``RawEntry`` per row."""

    def __init__(self, *, csv_path: Path) -> None:
        """Build a Crunchbase CSV source.

        Args:
            csv_path: Filesystem path to the Crunchbase organizations CSV export.
        """
        self.csv_path = csv_path

    async def fetch(self) -> AsyncIterator[RawEntry]:
        """Yield one ``RawEntry`` per CSV row that carries an ID or company name."""
        with self.csv_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                name = _first_present(row, _NAME_COLS)
                ident = _first_present(row, _ID_COLS) or name
                if not ident:
                    continue
                url = _first_present(row, _URL_COLS) or None
                desc = _first_present(row, _DESC_COLS) or None
                yield RawEntry(
                    source="crunchbase_csv",
                    source_id=ident,
                    url=url,
                    raw_html=None,
                    markdown_text=desc,
                    fetched_at=datetime.now(UTC),
                )
