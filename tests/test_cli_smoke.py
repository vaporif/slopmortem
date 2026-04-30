"""CLI smoke tests via ``typer.testing.CliRunner``.

Covers Task 10 plan steps:
- 10.7 ``replay --dataset`` with no fixture exits with code 2.
- CLI smoke for ``query`` with ``run_query`` monkeypatched to return a fixture
  Report.

``slopmortem.cli._build_deps`` is the monkeypatch seam, so the smoke tests
don't need real Qdrant / OpenRouter / OpenAI credentials.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from typer.testing import CliRunner

from slopmortem.budget import Budget
from slopmortem.cli import app
from slopmortem.models import (
    InputContext,
    PerspectiveScore,
    PipelineMeta,
    Report,
    SimilarityScores,
    Synthesis,
)

if TYPE_CHECKING:
    import pytest

    from slopmortem.config import Config


def _fixture_report(*, name: str = "Foo") -> Report:
    sim = SimilarityScores(
        business_model=PerspectiveScore(score=7.0, rationale="match"),
        market=PerspectiveScore(score=6.0, rationale="match"),
        gtm=PerspectiveScore(score=5.0, rationale="match"),
        stage_scale=PerspectiveScore(score=4.0, rationale="match"),
    )
    syn = Synthesis(
        candidate_id="acme",
        name="Acme",
        one_liner="One-line summary.",
        failure_date=None,
        lifespan_months=None,
        similarity=sim,
        why_similar="Similar.",
        where_diverged="Diverged.",
        failure_causes=["one"],
        lessons_for_input=["one"],
        sources=[],
    )
    return Report(
        input=InputContext(name=name, description="A pitch", years_filter=None),
        generated_at=datetime.now(UTC),
        candidates=[syn],
        pipeline_meta=PipelineMeta(
            K_retrieve=30,
            N_synthesize=5,
            models={"facet": "f", "rerank": "r", "synthesize": "s"},
            cost_usd_total=0.0,
            latency_ms_total=0,
            trace_id=None,
            budget_remaining_usd=2.0,
            budget_exceeded=False,
        ),
    )


def _build_fake_deps(_config: Config) -> tuple[object, object, object, Budget]:
    """Stand-in for :func:`slopmortem.cli._build_deps`; returns inert objects."""
    # Bare ``object()`` instances are fine here — the smoke test patches
    # ``run_query``, so these are never called.
    return object(), object(), object(), Budget(cap_usd=0.0)


def _noop_set_corpus(_corpus: object) -> None:
    """Stand-in for :func:`slopmortem.corpus.tools_impl._set_corpus`."""
    return


def test_query_smoke_renders_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """``slopmortem query`` runs end-to-end with a fake ``run_query``.

    Checks typer wiring: arg parsing, dispatch through ``_query``, render to
    stdout, with ``_build_deps`` as the seam.
    """

    async def _fake_run_query(input_ctx: InputContext, **_kwargs: Any) -> Report:
        return _fixture_report(name=input_ctx.name)

    # Swap dep-construction so we don't need real OPENROUTER_API_KEY etc.
    monkeypatch.setattr("slopmortem.cli._build_deps", _build_fake_deps)
    monkeypatch.setattr("slopmortem.cli._set_corpus", _noop_set_corpus)
    monkeypatch.setattr("slopmortem.cli.run_query", _fake_run_query)

    runner = CliRunner()
    result = runner.invoke(app, ["query", "Some pitch text", "--name", "MyStartup"])
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    # The rendered report's title should contain the input name.
    assert "MyStartup" in result.stdout


def test_query_smoke_default_unnamed(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``--name`` is omitted, the rendered report uses ``(unnamed)``."""

    async def _fake_run_query(input_ctx: InputContext, **_kwargs: Any) -> Report:
        return _fixture_report(name=input_ctx.name)

    monkeypatch.setattr("slopmortem.cli._build_deps", _build_fake_deps)
    monkeypatch.setattr("slopmortem.cli._set_corpus", _noop_set_corpus)
    monkeypatch.setattr("slopmortem.cli.run_query", _fake_run_query)

    runner = CliRunner()
    result = runner.invoke(app, ["query", "A pitch"])
    assert result.exit_code == 0, result.stdout + (result.stderr or "")
    assert "(unnamed)" in result.stdout


def test_replay_missing_dataset_exits_with_code_2() -> None:
    """``replay`` against a missing dataset exits cleanly with code 2."""
    runner = CliRunner()
    result = runner.invoke(app, ["replay", "does-not-exist"])
    assert result.exit_code == 2
    # Error goes to stderr; click's CliRunner merges streams unless
    # ``mix_stderr=False`` is set. Either way "no dataset" lands in the
    # captured output.
    combined = (result.stdout or "") + (result.stderr or "")
    assert "no dataset" in combined
