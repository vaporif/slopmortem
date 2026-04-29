"""CLI tests for ``slopmortem ingest``: wiring assembled, orchestrator dispatched."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

from slopmortem.cli import app

if TYPE_CHECKING:
    from pathlib import Path


def _fake_deps(*_args: object, **_kwargs: object) -> tuple[Any, ...]:
    """Return six MagicMock placeholders matching ``_build_ingest_deps``'s tuple shape."""
    return (
        MagicMock(name="llm"),
        MagicMock(name="embed"),
        MagicMock(name="corpus"),
        MagicMock(name="budget"),
        MagicMock(name="journal"),
        MagicMock(name="slop"),
    )


def test_ingest_dry_run_dispatches_to_orchestrator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--dry-run path: real wiring assembled, ingest() called with dry_run=True."""
    fake_ingest = AsyncMock(return_value=MagicMock(dry_run=True, processed=0))
    monkeypatch.setattr("slopmortem.cli.ingest", fake_ingest)
    # Block real Qdrant / OpenRouter / OpenAI / sqlite construction:
    monkeypatch.setattr("slopmortem.cli._build_ingest_deps", _fake_deps)
    runner = CliRunner()
    result = runner.invoke(app, ["ingest", "--dry-run", "--post-mortems-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert fake_ingest.await_count == 1
    await_args = fake_ingest.await_args
    assert await_args is not None
    kwargs = await_args.kwargs
    assert kwargs["dry_run"] is True
    assert kwargs["force"] is False
    assert kwargs["post_mortems_root"] == tmp_path


def test_ingest_tavily_enrich_rejected(tmp_path: Path) -> None:
    """--tavily-enrich is deferred; CLI must error out cleanly, not silently no-op."""
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["ingest", "--tavily-enrich", "--post-mortems-root", str(tmp_path)],
    )
    assert result.exit_code != 0
    combined = result.output + (result.stderr or "")
    assert "Tavily" in combined
    assert "deferred" in combined


@pytest.mark.parametrize("flag", ["--reconcile", "--reclassify", "--list-review"])
def test_ingest_deferred_flags_rejected(flag: str, tmp_path: Path) -> None:
    """--reconcile / --reclassify / --list-review are separate paths, not in this task."""
    runner = CliRunner()
    result = runner.invoke(app, ["ingest", flag, "--post-mortems-root", str(tmp_path)])
    assert result.exit_code != 0
    combined = (result.output + (result.stderr or "")).lower()
    assert flag.lstrip("-") in combined or "deferred" in combined


def test_ingest_with_crunchbase_csv_appends_source(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When --crunchbase-csv is given, the sources list includes CrunchbaseCsvSource."""
    captured: dict[str, object] = {}

    async def fake_ingest(**kwargs: object) -> object:
        captured["sources"] = kwargs["sources"]
        return MagicMock(dry_run=True, processed=0)

    monkeypatch.setattr("slopmortem.cli.ingest", fake_ingest)
    monkeypatch.setattr("slopmortem.cli._build_ingest_deps", _fake_deps)
    csv = tmp_path / "cb.csv"
    csv.write_text("name,description\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "ingest",
            "--dry-run",
            "--crunchbase-csv",
            str(csv),
            "--post-mortems-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    sources = captured["sources"]
    assert isinstance(sources, list)
    source_classnames = [type(s).__name__ for s in sources]
    assert "CrunchbaseCsvSource" in source_classnames
    assert "CuratedSource" in source_classnames
    assert "HNAlgoliaSource" in source_classnames
