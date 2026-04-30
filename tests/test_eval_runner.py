"""Integration tests for :mod:`slopmortem.evals.runner`.

Invokes ``main(argv)`` directly, never spawns a subprocess. Tests run the
deterministic path (no env vars, no Qdrant, no real API calls) by
monkeypatching ``run_query`` to return a hand-built Report.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pytest

from slopmortem.evals import runner
from slopmortem.models import (
    InputContext,
    PerspectiveScore,
    PipelineMeta,
    Report,
    SimilarityScores,
    Synthesis,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path

    from slopmortem.budget import Budget
    from slopmortem.config import Config
    from slopmortem.corpus.store import Corpus
    from slopmortem.llm.client import LLMClient
    from slopmortem.llm.embedding_client import EmbeddingClient


def _make_synth(
    *,
    candidate_id: str = "cand-0",
    where_diverged: str = "Pitch is web-first; analogue was mobile-only.",
    sources: list[str] | None = None,
    lifespan_months: int | None = 60,
) -> Synthesis:
    return Synthesis(
        candidate_id=candidate_id,
        name=candidate_id,
        one_liner="x",
        failure_date=date(2023, 1, 1),
        lifespan_months=lifespan_months,
        similarity=SimilarityScores(
            business_model=PerspectiveScore(score=7.0, rationale="x"),
            market=PerspectiveScore(score=6.0, rationale="x"),
            gtm=PerspectiveScore(score=5.0, rationale="x"),
            stage_scale=PerspectiveScore(score=4.0, rationale="x"),
        ),
        why_similar="match",
        where_diverged=where_diverged,
        failure_causes=["x"],
        lessons_for_input=["x"],
        sources=sources if sources is not None else ["https://news.ycombinator.com/item?id=1"],
    )


def _make_report(ctx: InputContext, candidates: list[Synthesis]) -> Report:
    return Report(
        input=ctx,
        generated_at=datetime.now(UTC),
        candidates=candidates,
        pipeline_meta=PipelineMeta(
            K_retrieve=6,
            N_synthesize=3,
            models={"facet": "f", "rerank": "r", "synthesize": "s"},
            cost_usd_total=0.0,
            latency_ms_total=0,
            trace_id=None,
            budget_remaining_usd=2.0,
            budget_exceeded=False,
        ),
    )


def _write_seed(tmp_path: Path, rows: Sequence[Mapping[str, object]]) -> Path:
    p = tmp_path / "seed.jsonl"
    p.write_text("\n".join(json.dumps(dict(r)) for r in rows) + "\n")
    return p


def _patch_run_query(
    monkeypatch: pytest.MonkeyPatch,
    fn: Callable[[InputContext], Synthesis],
) -> None:
    """Replace ``slopmortem.evals.runner.run_query`` with a deterministic stub."""

    async def _fake(  # noqa: PLR0913
        input_ctx: InputContext,
        *,
        llm: LLMClient,
        embedding_client: EmbeddingClient,
        corpus: Corpus,
        config: Config,
        budget: Budget,
        progress: Callable[[str], None] | None = None,
    ) -> Report:
        del llm, embedding_client, corpus, config, budget, progress
        return _make_report(input_ctx, [fn(input_ctx)])

    # Runner calls run_query via its own module reference; patch there.
    monkeypatch.setattr("slopmortem.evals.runner.run_query", _fake)


def test_runner_exits_zero_when_baseline_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        {"name": "alpha", "description": "An alpha pitch."},
        {"name": "beta", "description": "A beta pitch."},
    ]
    seed = _write_seed(tmp_path, rows)

    _patch_run_query(monkeypatch, lambda _ctx: _make_synth(candidate_id="cand-0"))

    baseline = {
        "version": 1,
        "rows": {
            "alpha": {
                "candidates_count": 1,
                "assertions": {
                    "cand-0": {
                        "where_diverged_nonempty": True,
                        "all_sources_in_allowed_domains": True,
                        "lifespan_months_positive": True,
                        "claims_grounded_in_body": True,
                    }
                },
            },
            "beta": {
                "candidates_count": 1,
                "assertions": {
                    "cand-0": {
                        "where_diverged_nonempty": True,
                        "all_sources_in_allowed_domains": True,
                        "lifespan_months_positive": True,
                        "claims_grounded_in_body": True,
                    }
                },
            },
        },
    }
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline))

    with pytest.raises(SystemExit) as exc_info:
        runner.main(
            [
                "--dataset",
                str(seed),
                "--baseline",
                str(baseline_path),
            ]
        )
    assert exc_info.value.code == 0


def test_runner_exits_nonzero_on_regression(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = [{"name": "alpha", "description": "An alpha pitch."}]
    seed = _write_seed(tmp_path, rows)

    # Patched run_query returns a Synthesis with empty where_diverged. The
    # baseline expects True, so this counts as a regression.
    _patch_run_query(
        monkeypatch,
        lambda _ctx: _make_synth(candidate_id="cand-0", where_diverged=""),
    )

    baseline = {
        "version": 1,
        "rows": {
            "alpha": {
                "candidates_count": 1,
                "assertions": {
                    "cand-0": {
                        "where_diverged_nonempty": True,
                        "all_sources_in_allowed_domains": True,
                        "lifespan_months_positive": True,
                        "claims_grounded_in_body": True,
                    }
                },
            },
        },
    }
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline))

    with pytest.raises(SystemExit) as exc_info:
        runner.main(
            [
                "--dataset",
                str(seed),
                "--baseline",
                str(baseline_path),
            ]
        )
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "where_diverged_nonempty" in captured.err


def test_runner_record_flag_invokes_helper(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """--record dispatches to record_cassettes_for_inputs via asyncio.run."""
    seed = _write_seed(tmp_path, [{"name": "alpha", "description": "x"}])
    baseline = tmp_path / "baseline.json"
    baseline.write_text("{}")

    seen: dict[str, object] = {}

    async def fake_helper(
        *,
        inputs: list[InputContext],
        output_dir: Path,
        corpus_fixture_path: Path,
        config: Config,
        max_cost_usd: float,
    ) -> None:
        del output_dir, corpus_fixture_path, config
        seen["called"] = True
        seen["inputs"] = inputs
        seen["max_cost_usd"] = max_cost_usd

    monkeypatch.setattr(
        "slopmortem.evals.recording_helper.record_cassettes_for_inputs",
        fake_helper,
    )
    # Stub the corpus fixture so the existence check passes.
    monkeypatch.chdir(tmp_path)
    (tmp_path / "tests" / "fixtures").mkdir(parents=True, exist_ok=True)
    (tmp_path / "tests" / "fixtures" / "corpus_fixture.jsonl").write_text("")

    with pytest.raises(SystemExit) as exc_info:
        runner.main(
            [
                "--dataset",
                str(seed),
                "--baseline",
                str(baseline),
                "--record",
                "--max-cost-usd",
                "1.5",
            ]
        )
    assert exc_info.value.code == 0
    assert seen["called"] is True
    assert seen["max_cost_usd"] == pytest.approx(1.5)


def test_runner_writes_baseline_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing baseline gets created via --write-baseline; exit 0, no regressions."""
    rows = [{"name": "alpha", "description": "An alpha pitch."}]
    seed = _write_seed(tmp_path, rows)

    _patch_run_query(monkeypatch, lambda _ctx: _make_synth(candidate_id="cand-0"))

    baseline_path = tmp_path / "baseline-new.json"
    assert not baseline_path.exists()

    with pytest.raises(SystemExit) as exc_info:
        runner.main(
            [
                "--dataset",
                str(seed),
                "--baseline",
                str(baseline_path),
                "--write-baseline",
            ]
        )
    assert exc_info.value.code == 0
    assert baseline_path.exists()
    written = json.loads(baseline_path.read_text())
    assert written["version"] == 1
    assert "alpha" in written["rows"]
