"""Unit tests for the three eval assertions in :mod:`slopmortem.evals.assertions`.

Each assertion is a pure function over a :class:`Synthesis`; these tests build
the smallest valid Synthesis values and assert on the boolean output.
"""

from datetime import date

from slopmortem.evals.assertions import (
    all_sources_in_allowed_domains,
    lifespan_months_positive,
    where_diverged_nonempty,
)
from slopmortem.models import PerspectiveScore, SimilarityScores, Synthesis


def _synth(
    *,
    where_diverged: str = "Pitch is web-first; analogue was mobile-only.",
    sources: list[str] | None = None,
    lifespan_months: int | None = 60,
) -> Synthesis:
    """Return a minimal valid Synthesis with the given overrides."""
    return Synthesis(
        candidate_id="cand-0",
        name="cand-0",
        one_liner="A B2B fintech that died.",
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
        failure_causes=["CAC > LTV"],
        lessons_for_input=["target larger ACVs"],
        sources=sources if sources is not None else [],
    )


# ---------------------------------------------------------------------------
# where_diverged_nonempty
# ---------------------------------------------------------------------------


def test_where_diverged_nonempty_empty_string_is_false() -> None:
    assert where_diverged_nonempty(_synth(where_diverged="")) is False


def test_where_diverged_nonempty_whitespace_only_is_false() -> None:
    assert where_diverged_nonempty(_synth(where_diverged="   \n  \t")) is False


def test_where_diverged_nonempty_real_text_is_true() -> None:
    assert where_diverged_nonempty(_synth(where_diverged="diverged here")) is True


# ---------------------------------------------------------------------------
# all_sources_in_allowed_domains
# ---------------------------------------------------------------------------


def test_all_sources_empty_is_vacuously_true() -> None:
    s = _synth(sources=[])
    assert all_sources_in_allowed_domains(s, {"news.ycombinator.com"}) is True


def test_all_sources_in_allowlist_is_true() -> None:
    s = _synth(
        sources=[
            "https://news.ycombinator.com/item?id=1",
            "https://example.com/post",
        ]
    )
    allowed = {"news.ycombinator.com", "example.com"}
    assert all_sources_in_allowed_domains(s, allowed) is True


def test_all_sources_one_outside_allowlist_is_false() -> None:
    s = _synth(
        sources=[
            "https://news.ycombinator.com/item?id=1",
            "https://malicious.example/post",
        ]
    )
    allowed = {"news.ycombinator.com"}
    assert all_sources_in_allowed_domains(s, allowed) is False


def test_all_sources_unparseable_url_is_false() -> None:
    # ``urlparse("not a url").hostname`` is ``None``; runner must treat that as a miss.
    s = _synth(sources=["not a url at all"])
    allowed = {"news.ycombinator.com"}
    assert all_sources_in_allowed_domains(s, allowed) is False


# ---------------------------------------------------------------------------
# lifespan_months_positive
# ---------------------------------------------------------------------------


def test_lifespan_none_is_true() -> None:
    assert lifespan_months_positive(_synth(lifespan_months=None)) is True


def test_lifespan_positive_is_true() -> None:
    assert lifespan_months_positive(_synth(lifespan_months=12)) is True


def test_lifespan_zero_is_false() -> None:
    assert lifespan_months_positive(_synth(lifespan_months=0)) is False


def test_lifespan_negative_is_false() -> None:
    assert lifespan_months_positive(_synth(lifespan_months=-3)) is False
