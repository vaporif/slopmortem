"""``--reconcile`` dispatches to ``corpus._reconcile.reconcile`` and prints the report."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from typer.testing import CliRunner

from slopmortem.cli import app
from slopmortem.corpus import ReconcileReport, ReconcileRow

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


async def _fake_journal(*_a: object, **_k: object) -> MagicMock:
    return MagicMock()


async def _fake_corpus(*_a: object, **_k: object) -> MagicMock:
    return MagicMock()


def _patch_narrow_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bypass the journal + corpus builders so the reconcile path needs no env vars."""
    monkeypatch.setattr("slopmortem.cli._ingest_cmd._build_journal", _fake_journal)
    monkeypatch.setattr("slopmortem.cli._ingest_cmd._build_ingest_corpus", _fake_corpus)


def test_cli_reconcile_dispatches_with_repair_true(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``--reconcile`` calls ``reconcile()`` with ``repair=True`` and exits 0."""
    fake_report = ReconcileReport(rows=[], applied=[])
    fake_reconcile = AsyncMock(return_value=fake_report)
    monkeypatch.setattr("slopmortem.cli._ingest_cmd.reconcile", fake_reconcile)
    _patch_narrow_deps(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["ingest", "--reconcile", "--post-mortems-root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    fake_reconcile.assert_awaited_once()
    await_args = fake_reconcile.await_args
    assert await_args is not None
    assert await_args.kwargs.get("repair") is True
    assert "reconcile" in result.output.lower()


def test_cli_reconcile_prints_drift_findings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When the report has rows, each one shows up in the printed output."""
    fake_report = ReconcileReport(
        rows=[
            ReconcileRow(
                drift_class="a",
                canonical_id="canonical/abc",
                source="hn",
                source_id="123",
                path="canonical/abc.md",
                detail="re-embed",
            ),
            ReconcileRow(
                drift_class="e",
                canonical_id=None,
                source=None,
                source_id=None,
                path="canonical/abc.md.tmp",
                detail="orphan tmp file",
            ),
        ],
        applied=["a:canonical/abc.md", "e:canonical/abc.md.tmp"],
    )
    monkeypatch.setattr("slopmortem.cli._ingest_cmd.reconcile", AsyncMock(return_value=fake_report))
    _patch_narrow_deps(monkeypatch)

    runner = CliRunner()
    result = runner.invoke(app, ["ingest", "--reconcile", "--post-mortems-root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "drift_class=a" in result.output or "class=a" in result.output
    assert "canonical/abc.md.tmp" in result.output
