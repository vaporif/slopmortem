"""--reclassify dispatches to slopmortem.corpus.reclassify.reclassify_quarantined."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from typer.testing import CliRunner

from slopmortem.cli import app
from slopmortem.models import ReclassifyReport

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _fake_journal(*_a: object, **_k: object) -> MagicMock:
    return MagicMock()


def _fake_classifier(*_a: object, **_k: object) -> MagicMock:
    return MagicMock()


def test_cli_reclassify_dispatches(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--reclassify wires journal+classifier into reclassify_quarantined and prints the report."""
    fake_report = ReclassifyReport(total=3, declassified=1, still_slop=2, errors=0)
    fake_reclassify = AsyncMock(return_value=fake_report)
    monkeypatch.setattr("slopmortem.cli.reclassify_quarantined", fake_reclassify)
    monkeypatch.setattr("slopmortem.cli._build_journal", _fake_journal)
    monkeypatch.setattr("slopmortem.cli._build_slop_classifier", _fake_classifier)

    runner = CliRunner()
    result = runner.invoke(app, ["ingest", "--reclassify", "--post-mortems-root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    fake_reclassify.assert_awaited_once()
    assert "declassified=1" in result.output
    assert "still_slop=2" in result.output
    assert "total=3" in result.output
    assert "errors=0" in result.output
