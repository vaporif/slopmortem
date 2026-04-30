"""Pure unit tests for ``slopmortem.stages.cluster_lessons``.

Covers the contract documented in the v1 brief: greedy single-pass Jaccard
clustering at threshold 0.5, deterministic frequency-desc / summary-asc sort,
empty inputs, paraphrase merging, stop-word resilience, and per-candidate
dedup of identical lesson text.
"""

from __future__ import annotations

from datetime import date

from slopmortem.models import (
    PerspectiveScore,
    SimilarityScores,
    Synthesis,
)
from slopmortem.stages.cluster_lessons import cluster_lessons


def _scores(value: float = 5.0) -> SimilarityScores:
    return SimilarityScores(
        business_model=PerspectiveScore(score=value, rationale="bm"),
        market=PerspectiveScore(score=value, rationale="market"),
        gtm=PerspectiveScore(score=value, rationale="gtm"),
        stage_scale=PerspectiveScore(score=value, rationale="stage"),
    )


def _synthesis(*, candidate_id: str, name: str, lessons: list[str]) -> Synthesis:
    return Synthesis(
        candidate_id=candidate_id,
        name=name,
        one_liner=f"{name} one-liner",
        failure_date=date(2023, 1, 1),
        lifespan_months=60,
        similarity=_scores(),
        why_similar="why",
        where_diverged="diverged",
        failure_causes=["cause"],
        lessons_for_input=lessons,
        sources=[],
    )


def test_empty_input_returns_empty_top_risks() -> None:
    result = cluster_lessons([])
    assert result.clusters == []


def test_single_synthesis_distinct_lessons_yield_one_cluster_each() -> None:
    syn = _synthesis(
        candidate_id="acme",
        name="Acme",
        lessons=[
            "target larger ACVs",
            "avoid SMB churn",
            "hire enterprise sales early",
        ],
    )
    result = cluster_lessons([syn])
    assert len(result.clusters) == 3
    for cluster in result.clusters:
        assert cluster.frequency == 1
        assert cluster.candidate_ids == ["acme"]


def test_inflection_difference_does_not_merge_without_stemming() -> None:
    """V1 is bag-of-words without stemming, so "segregate" != "segregated".

    Documenting the limitation: the brief's canonical "segregate customer
    assets" / "customer assets must be segregated" pairing actually scores
    Jaccard = 2/6 ≈ 0.33 (below 0.5 threshold) because "segregate" and
    "segregated" are distinct tokens. Stemming is a follow-up task.
    """
    a = _synthesis(
        candidate_id="acme",
        name="Acme",
        lessons=["segregate customer assets"],
    )
    b = _synthesis(
        candidate_id="beta",
        name="Beta",
        lessons=["customer assets must be segregated"],
    )
    result = cluster_lessons([a, b])
    assert len(result.clusters) == 2


def test_paraphrased_lessons_with_shared_tokens_merge() -> None:
    """Two phrasings that share enough tokens (>=0.5 Jaccard) should cluster.

    "segregate customer assets" → {segregate, customer, assets}
    "always segregate customer assets" → {always, segregate, customer, assets}
    Jaccard = 3/4 = 0.75, well above threshold.
    """
    a = _synthesis(
        candidate_id="acme",
        name="Acme",
        lessons=["segregate customer assets"],
    )
    b = _synthesis(
        candidate_id="beta",
        name="Beta",
        lessons=["always segregate customer assets"],
    )
    result = cluster_lessons([a, b])
    assert len(result.clusters) == 1
    cluster = result.clusters[0]
    assert cluster.frequency == 2
    assert sorted(cluster.candidate_ids) == ["acme", "beta"]
    # Shortest member's original text wins.
    assert cluster.summary == "segregate customer assets"


def test_unrelated_lessons_do_not_merge() -> None:
    a = _synthesis(
        candidate_id="acme",
        name="Acme",
        lessons=["target larger ACVs"],
    )
    b = _synthesis(
        candidate_id="beta",
        name="Beta",
        lessons=["avoid SMB churn"],
    )
    result = cluster_lessons([a, b])
    assert len(result.clusters) == 2
    summaries = {c.summary for c in result.clusters}
    assert summaries == {"target larger ACVs", "avoid SMB churn"}


def test_cluster_sort_order_frequency_desc() -> None:
    # Three syntheses raise a shared lesson; one raises a unique lesson.
    shared = "regulatory engagement is critical"
    syns = [
        _synthesis(candidate_id="a", name="A", lessons=[shared]),
        _synthesis(candidate_id="b", name="B", lessons=[shared, "unique to B"]),
        _synthesis(candidate_id="c", name="C", lessons=[shared]),
    ]
    result = cluster_lessons(syns)
    assert len(result.clusters) == 2
    # Highest-frequency cluster first.
    assert result.clusters[0].frequency == 3
    assert result.clusters[0].summary == shared
    assert result.clusters[1].frequency == 1


def test_same_lesson_twice_from_same_candidate_counted_once() -> None:
    syn = _synthesis(
        candidate_id="acme",
        name="Acme",
        lessons=["target larger ACVs", "target larger ACVs"],
    )
    result = cluster_lessons([syn])
    assert len(result.clusters) == 1
    assert result.clusters[0].frequency == 1
    assert result.clusters[0].candidate_ids == ["acme"]


def test_stop_word_resilience() -> None:
    """Stop-word removal lets "the team must focus on regulation" merge with "focus on regulation".

    After normalization:
      - {team, must, focus, regulation}
      - {focus, regulation}
    Jaccard = 2/4 = 0.5, exactly the threshold (inclusive).
    """
    a = _synthesis(
        candidate_id="acme",
        name="Acme",
        lessons=["the team must focus on regulation"],
    )
    b = _synthesis(
        candidate_id="beta",
        name="Beta",
        lessons=["focus on regulation"],
    )
    result = cluster_lessons([a, b])
    assert len(result.clusters) == 1
    cluster = result.clusters[0]
    assert cluster.frequency == 2
    assert sorted(cluster.candidate_ids) == ["acme", "beta"]
    # Shortest original text wins as canonical summary.
    assert cluster.summary == "focus on regulation"


def test_summary_picks_shortest_original_text() -> None:
    a = _synthesis(
        candidate_id="acme",
        name="Acme",
        lessons=["enforce strict customer asset segregation policies"],
    )
    b = _synthesis(
        candidate_id="beta",
        name="Beta",
        lessons=["enforce customer asset segregation"],
    )
    result = cluster_lessons([a, b])
    assert len(result.clusters) == 1
    assert result.clusters[0].summary == "enforce customer asset segregation"


def test_empty_token_lessons_get_their_own_clusters() -> None:
    """A lesson that normalizes to an empty token set must seed its own cluster."""
    syn = _synthesis(
        candidate_id="acme",
        name="Acme",
        lessons=["...", "the of and"],  # all-punct, all-stop-word
    )
    result = cluster_lessons([syn])
    # Each empty-token lesson becomes its own cluster (never merged with
    # anything, including each other, since centroid Jaccard is 0).
    assert len(result.clusters) == 2
