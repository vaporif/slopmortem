"""Integration tests for :mod:`slopmortem.evals.runner`.

Invokes ``main(argv)`` directly, never spawns a subprocess. Tests run the
deterministic path (no env vars, no Qdrant, no real API calls) by
monkeypatching ``runner._run_cassettes`` — the cassette stage short-circuits
to ``FAIL <rid>: no cassettes`` before reaching ``run_query`` when the
cassette dir is missing, so patching ``run_query`` itself never takes effect.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pytest

from slopmortem.evals import runner
from slopmortem.evals.recording_helper import RecordResult
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

    from slopmortem.config import Config


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
            min_similarity_score=4.0,
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


def _patch_run_cassettes(
    monkeypatch: pytest.MonkeyPatch,
    fn: Callable[[InputContext], Synthesis],
) -> None:
    """Replace ``runner._run_cassettes`` with a deterministic stub.

    Builds a hand-crafted ``Synthesis`` per row via *fn*, scores it through the
    runner's own ``_score_report`` so the diff/baseline machinery sees a
    realistic result dict, and honours ``scope_filter`` the same way the real
    function does.
    """

    async def _fake(
        rows: list[InputContext],
        row_ids: list[str],
        scope_filter: str | None,
    ) -> dict[str, dict[str, object]]:
        results: dict[str, dict[str, object]] = {}
        for ctx, rid in zip(rows, row_ids, strict=True):
            if scope_filter is not None and rid != scope_filter:
                continue
            report = _make_report(ctx, [fn(ctx)])
            results[rid] = runner._score_report(report, bodies_map={})
        return results

    monkeypatch.setattr("slopmortem.evals.runner._run_cassettes", _fake)


def test_runner_exits_zero_when_baseline_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        {"name": "alpha", "description": "An alpha pitch."},
        {"name": "beta", "description": "A beta pitch."},
    ]
    seed = _write_seed(tmp_path, rows)

    _patch_run_cassettes(monkeypatch, lambda _ctx: _make_synth(candidate_id="cand-0"))

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

    # Patched cassette stage returns a Synthesis with empty where_diverged.
    # The baseline expects True, so this counts as a regression.
    _patch_run_cassettes(
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

    async def fake_helper(  # noqa: PLR0913 — mirrors the helper's full kwarg surface
        *,
        inputs: list[InputContext],
        output_dir: Path,
        corpus_fixture_path: Path,
        config: Config,
        max_cost_usd: float,
        progress: object = None,
    ) -> RecordResult:
        del output_dir, corpus_fixture_path, config, progress
        seen["called"] = True
        seen["inputs"] = inputs
        seen["max_cost_usd"] = max_cost_usd
        return RecordResult(
            rows_total=len(inputs),
            rows_succeeded=len(inputs),
            cassettes_written=0,
            total_cost_usd=0.0,
        )

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


def test_runner_record_scope_matches_sha1_row_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """--record --scope <sha1> matches anonymous rows via _row_id, not name."""
    import hashlib  # noqa: PLC0415

    description = "Anonymous pitch with no name."
    sha1_id = hashlib.sha1(description.encode(), usedforsecurity=False).hexdigest()[:8]
    seed = _write_seed(tmp_path, [{"name": "", "description": description}])
    baseline = tmp_path / "baseline.json"
    baseline.write_text("{}")

    seen: dict[str, object] = {}

    async def fake_helper(  # noqa: PLR0913
        *,
        inputs: list[InputContext],
        output_dir: Path,
        corpus_fixture_path: Path,
        config: Config,
        max_cost_usd: float,
        progress: object = None,
    ) -> RecordResult:
        del output_dir, corpus_fixture_path, config, max_cost_usd, progress
        seen["inputs"] = inputs
        return RecordResult(
            rows_total=len(inputs),
            rows_succeeded=len(inputs),
            cassettes_written=0,
            total_cost_usd=0.0,
        )

    monkeypatch.setattr(
        "slopmortem.evals.recording_helper.record_cassettes_for_inputs",
        fake_helper,
    )
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
                "--scope",
                sha1_id,
            ]
        )
    assert exc_info.value.code == 0
    inputs = seen["inputs"]
    assert isinstance(inputs, list)
    assert len(inputs) == 1
    assert inputs[0].description == description


def test_runner_writes_baseline_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing baseline gets created via --write-baseline; exit 0, no regressions."""
    rows = [{"name": "alpha", "description": "An alpha pitch."}]
    seed = _write_seed(tmp_path, rows)

    _patch_run_cassettes(monkeypatch, lambda _ctx: _make_synth(candidate_id="cand-0"))

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
