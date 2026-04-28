from pathlib import Path

import pytest

from slopmortem.corpus.paths import safe_path


def test_safe_path_canonical(tmp_path: Path):
    p = safe_path(tmp_path, kind="canonical", text_id="0123456789abcdef")
    assert p == tmp_path / "canonical" / "0123456789abcdef.md"


def test_safe_path_raw_requires_source(tmp_path: Path):
    with pytest.raises(ValueError):
        safe_path(tmp_path, kind="raw", text_id="0123456789abcdef")


def test_safe_path_canonical_rejects_source(tmp_path: Path):
    with pytest.raises(ValueError):
        safe_path(tmp_path, kind="canonical", text_id="0123456789abcdef", source="hn")


def test_safe_path_rejects_traversal(tmp_path: Path):
    with pytest.raises(ValueError):
        safe_path(tmp_path, kind="canonical", text_id="../etc/passwd")


def test_safe_path_rejects_bad_text_id(tmp_path: Path):
    with pytest.raises(ValueError):
        safe_path(tmp_path, kind="canonical", text_id="not-a-hash")


def test_safe_path_rejects_unknown_kind(tmp_path: Path):
    with pytest.raises(ValueError):
        safe_path(tmp_path, kind="other", text_id="0123456789abcdef")
