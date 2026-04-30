"""Schema validation for the v0 curated YAML scaffold.

Pins the row schema (per plan §1903-1914 and spec lines 1029-1031) and asserts
that every sector value lives in ``slopmortem/corpus/taxonomy.yml``. Real URLs
and real ``content_sha256_at_review`` are populated by Task 4b.
"""

from __future__ import annotations

from pathlib import Path

import yaml

CURATED_YAML = (
    Path(__file__).parent.parent.parent
    / "slopmortem"
    / "corpus"
    / "sources"
    / "curated"
    / "post_mortems_v0.yml"
)
TAXONOMY_YAML = Path(__file__).parent.parent.parent / "slopmortem" / "corpus" / "taxonomy.yml"

REQUIRED_FIELDS = {
    "url",
    "startup_name",
    "sector",
    "submitted_by",
    "reviewed_by",
    "content_sha256_at_review",
}


def _load_rows() -> list[dict[str, object]]:
    with CURATED_YAML.open("r", encoding="utf-8") as fh:
        rows = yaml.safe_load(fh) or []
    assert isinstance(rows, list)
    return rows


def _load_sectors() -> set[str]:
    with TAXONOMY_YAML.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return set(data["sector"])


def test_v0_has_at_least_50_rows() -> None:
    rows = _load_rows()
    assert len(rows) >= 50, f"expected >= 50 rows, got {len(rows)}"


def test_every_row_has_required_fields() -> None:
    rows = _load_rows()
    for i, row in enumerate(rows):
        missing = REQUIRED_FIELDS - row.keys()
        assert not missing, f"row {i} missing fields: {missing}"


def test_every_sector_value_in_taxonomy() -> None:
    rows = _load_rows()
    sectors = _load_sectors()
    for i, row in enumerate(rows):
        assert row["sector"] in sectors, f"row {i} sector {row['sector']!r} not in taxonomy"


def test_sector_coverage_at_least_5_per_sector_for_top_10() -> None:
    """Plan §1903: 5 URLs per sector across 10 sectors. Verifies the matrix."""
    rows = _load_rows()
    counts: dict[str, int] = {}
    for row in rows:
        counts[str(row["sector"])] = counts.get(str(row["sector"]), 0) + 1
    sectors_with_at_least_five = {s for s, c in counts.items() if c >= 5}
    assert len(sectors_with_at_least_five) >= 10, (
        f"expected >=10 sectors with >=5 rows; got counts {counts}"
    )
