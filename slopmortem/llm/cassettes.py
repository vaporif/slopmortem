from __future__ import annotations

from pathlib import Path

CASSETTE_ROOT = Path(__file__).resolve().parent.parent.parent / "tests" / "cassettes"


def cassette_dir_for(test_file: str | Path) -> Path:
    """Return the on-disk directory pytest-recording should use for a given
    test file. We mirror the test path under ``tests/cassettes/``.
    """
    p = Path(test_file).resolve()
    try:
        rel = p.relative_to(CASSETTE_ROOT.parent)
    except ValueError:
        rel = Path(p.name)
    return CASSETTE_ROOT / rel.with_suffix("")
