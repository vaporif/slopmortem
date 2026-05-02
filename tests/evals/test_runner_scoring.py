"""Pure-function unit tests for the eval-runner scoring helpers.

Decoupled from cassettes / Qdrant: builds Synthesis instances directly and
exercises ``_allowed_hosts_for_candidate`` / ``_score_synthesis`` /
``_score_report`` with hand-built ``bodies_map`` mappings.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from slopmortem.evals.runner import (
    _allowed_hosts_for_candidate,
    _score_report,
    _score_synthesis,
)
from slopmortem.models import (
    InputContext,
    PerspectiveScore,
    PipelineMeta,
    Report,
    SimilarityScores,
    Synthesis,
)


def _synthesis(*, candidate_id: str, sources: list[str]) -> Synthesis:
    return Synthesis(
        candidate_id=candidate_id,
        name=candidate_id,
        one_liner="x",
        failure_date=date(2023, 1, 1),
        lifespan_months=60,
        similarity=SimilarityScores(
            business_model=PerspectiveScore(score=5.0, rationale="x"),
            market=PerspectiveScore(score=5.0, rationale="x"),
            gtm=PerspectiveScore(score=5.0, rationale="x"),
            stage_scale=PerspectiveScore(score=5.0, rationale="x"),
        ),
        why_similar="x",
        where_diverged="differs in x",
        failure_causes=["a"],
        lessons_for_input=["b"],
        sources=sources,
    )


def test_allowed_hosts_unions_fixed_with_synthesis_sources() -> None:
    s = _synthesis(
        candidate_id="cand-1",
        sources=["https://example.com/a", "https://blog.example.org/b"],
    )
    hosts = _allowed_hosts_for_candidate(s)
    assert "news.ycombinator.com" in hosts  # fixed
    assert "example.com" in hosts
    assert "blog.example.org" in hosts


def test_allowed_hosts_with_no_synthesis_sources_is_fixed_only() -> None:
    s = _synthesis(candidate_id="cand-1", sources=[])
    hosts = _allowed_hosts_for_candidate(s)
    assert hosts == {"news.ycombinator.com"}


def test_score_synthesis_treats_missing_body_as_vacuously_grounded() -> None:
    s = _synthesis(candidate_id="cand-1", sources=["https://news.ycombinator.com/x"])
    result = _score_synthesis(s, bodies_map={"cand-1": None})
    assert result["claims_grounded_in_body"] is True


def test_score_report_emits_baseline_shape() -> None:
    s1 = _synthesis(candidate_id="cand-1", sources=["https://news.ycombinator.com/x"])
    s2 = _synthesis(candidate_id="cand-2", sources=["https://news.ycombinator.com/y"])
    report = Report(
        input=InputContext(name="x", description="y"),
        generated_at=datetime.now(UTC),
        candidates=[s1, s2],
        pipeline_meta=PipelineMeta(
            K_retrieve=10,
            N_synthesize=2,
            min_similarity_score=4.0,
            cost_usd_total=0.0,
            latency_ms_total=0,
            budget_exceeded=False,
            budget_remaining_usd=2.0,
            trace_id=None,
            models={"facet": "x", "rerank": "y", "synthesize": "z"},
        ),
    )
    result = _score_report(report, bodies_map={"cand-1": None, "cand-2": None})
    assert result["candidates_count"] == 2
    assertions = result["assertions"]
    assert isinstance(assertions, dict)
    assert set(assertions.keys()) == {"cand-1", "cand-2"}
    for per_candidate in assertions.values():
        assert set(per_candidate.keys()) == {
            "where_diverged_nonempty",
            "all_sources_in_allowed_domains",
            "lifespan_months_positive",
            "claims_grounded_in_body",
        }
