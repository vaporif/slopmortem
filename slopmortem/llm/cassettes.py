"""On-disk cassette path discovery for pytest-recording / VCR."""

from __future__ import annotations

from pathlib import Path

CASSETTE_ROOT = Path(__file__).resolve().parent.parent.parent / "tests" / "cassettes"


def cassette_dir_for(test_file: str | Path) -> Path:
    """On-disk directory pytest-recording should use for a given test file.

    The layout under ``tests/cassettes/`` mirrors the test tree so cassettes
    sit next to the tests that recorded them.
    """
    p = Path(test_file).resolve()
    try:
        rel = p.relative_to(CASSETTE_ROOT.parent)
    except ValueError:
        rel = Path(p.name)
    return CASSETTE_ROOT / rel.with_suffix("")
