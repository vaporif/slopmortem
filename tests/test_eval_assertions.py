"""Unit tests for the three eval assertions in `slopmortem.evals.assertions`.

Each assertion is a pure function over a `Synthesis`; these tests build
the smallest valid Synthesis values and assert on the boolean output.
"""

from __future__ import annotations

from datetime import date

from slopmortem.evals.assertions import (
    all_sources_in_allowed_domains,
    claims_grounded_in_body,
    lifespan_months_positive,
    where_diverged_nonempty,
)
from slopmortem.models import PerspectiveScore, SimilarityScores, Synthesis


def _synth(
    *,
    where_diverged: str = "Pitch is web-first; analogue was mobile-only.",
    sources: list[str] | None = None,
    lifespan_months: int | None = 60,
    why_similar: str = "match",
    similarity: SimilarityScores | None = None,
) -> Synthesis:
    """Return a minimal valid Synthesis with the given overrides."""
    if similarity is None:
        similarity = SimilarityScores(
            business_model=PerspectiveScore(score=7.0, rationale="x"),
            market=PerspectiveScore(score=6.0, rationale="x"),
            gtm=PerspectiveScore(score=5.0, rationale="x"),
            stage_scale=PerspectiveScore(score=4.0, rationale="x"),
        )
    return Synthesis(
        candidate_id="cand-0",
        name="cand-0",
        one_liner="A B2B fintech that died.",
        failure_date=date(2023, 1, 1),
        lifespan_months=lifespan_months,
        similarity=similarity,
        why_similar=why_similar,
        where_diverged=where_diverged,
        failure_causes=["CAC > LTV"],
        lessons_for_input=["target larger ACVs"],
        sources=sources if sources is not None else [],
    )


def _sim(
    *,
    business_model: str = "x",
    market: str = "x",
    gtm: str = "x",
    stage_scale: str = "x",
) -> SimilarityScores:
    """Build a SimilarityScores with custom rationales for grounding tests."""
    return SimilarityScores(
        business_model=PerspectiveScore(score=7.0, rationale=business_model),
        market=PerspectiveScore(score=6.0, rationale=market),
        gtm=PerspectiveScore(score=5.0, rationale=gtm),
        stage_scale=PerspectiveScore(score=4.0, rationale=stage_scale),
    )


def test_where_diverged_nonempty_empty_string_is_false() -> None:
    assert where_diverged_nonempty(_synth(where_diverged="")) is False


def test_where_diverged_nonempty_whitespace_only_is_false() -> None:
    assert where_diverged_nonempty(_synth(where_diverged="   \n  \t")) is False


def test_where_diverged_nonempty_real_text_is_true() -> None:
    assert where_diverged_nonempty(_synth(where_diverged="diverged here")) is True


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


def test_lifespan_none_is_true() -> None:
    assert lifespan_months_positive(_synth(lifespan_months=None)) is True


def test_lifespan_positive_is_true() -> None:
    assert lifespan_months_positive(_synth(lifespan_months=12)) is True


def test_lifespan_zero_is_false() -> None:
    assert lifespan_months_positive(_synth(lifespan_months=0)) is False


def test_lifespan_negative_is_false() -> None:
    assert lifespan_months_positive(_synth(lifespan_months=-3)) is False


# ----------------------------------------------------------------------------
# claims_grounded_in_body
# ----------------------------------------------------------------------------


def test_claims_grounded_empty_prose_is_vacuously_true() -> None:
    sim = _sim(business_model="", market="", gtm="", stage_scale="")
    s = _synth(why_similar="", similarity=sim)
    assert claims_grounded_in_body(s, "anything, even with 1.7 million") is True


def test_claims_grounded_no_digits_is_vacuously_true() -> None:
    # No numeric tokens in any rationale -> nothing to verify.
    s = _synth(why_similar="Both target SMB invoicing.", similarity=_sim())
    assert claims_grounded_in_body(s, "") is True


def test_claims_grounded_matching_number_in_body_is_true() -> None:
    s = _synth(why_similar="Reached 1.7 million customers before failing.")
    body = "Acme Corp reached 1.7 million customers before running out of runway."
    assert claims_grounded_in_body(s, body) is True


def test_claims_grounded_fabricated_qualifier_is_false() -> None:
    # Real-world failure: model added "US" qualifier; body lacks it.
    s = _synth(why_similar="Reached 1.7 million US customers before failing.")
    body = "Acme reached 1.7 million customers globally before failing."
    assert claims_grounded_in_body(s, body) is False


def test_claims_grounded_dollar_with_trailing_word_not_in_body_is_false() -> None:
    # "$12B at peak" -> regex extracts "$12B at"; body has "$12B" but not "$12B at".
    # Documents the actual regex behavior: trailing word is captured and checked.
    s = _synth(similarity=_sim(stage_scale="Held $12B at peak before collapse."))
    body = "Held ~$12B (May 2022) and $25B (peak) AUM before collapse."
    assert claims_grounded_in_body(s, body) is False


def test_claims_grounded_fabricated_leverage_ratio_is_false() -> None:
    # "80:1 leverage" -> findall returns ['80', '1 leverage']: the colon stops
    # the first digit cluster, then ``\d`` followed by ``\s+\w+`` re-matches on
    # ``1 leverage``. The function returns False on the first miss (``'80' not
    # in body``), so the second match is never substring-checked here.
    s = _synth(similarity=_sim(business_model="Ran 80:1 leverage at the end."))
    body = "Operated through traditional banking partnerships without disclosed ratios."
    assert claims_grounded_in_body(s, body) is False


def test_claims_grounded_empty_body_with_numeric_claim_is_false() -> None:
    s = _synth(why_similar="Reached 1.7 million customers.")
    assert claims_grounded_in_body(s, "") is False


def test_claims_grounded_scans_all_four_perspective_rationales() -> None:
    # Plant a fabricated number in each perspective in turn; each should fail.
    body = "Acme Corp had a long story."
    for kw in ("business_model", "market", "gtm", "stage_scale"):
        kwargs: dict[str, str] = {kw: "Hit 99 million users."}
        s = _synth(similarity=_sim(**kwargs))
        assert claims_grounded_in_body(s, body) is False, kw


def test_claims_grounded_trailing_period_does_not_break_match() -> None:
    # Sentence-final period must not become part of the extracted token.
    s = _synth(why_similar="Hit 5 million customers.")
    body = "They hit 5 million customers in year three."
    assert claims_grounded_in_body(s, body) is True


def test_claims_grounded_case_sensitive_regex_and_substring() -> None:
    # Pins the post-IGNORECASE-removal behavior so a future contributor doesn't
    # reintroduce ``re.IGNORECASE`` on _NUMERIC_CLAIM_RE. With the flag gone,
    # both the regex match and the substring check are case-sensitive and
    # consistent: lowercase synthesis prose (the common case) extracts a
    # full lowercase token that matches a lowercase body verbatim.
    #
    # Path A: lowercase prose vs lowercase body -> qualifier matches, full
    # token "1.7 million customers" extracted, found verbatim in body -> True.
    s_lower = _synth(why_similar="Reached 1.7 million customers.")
    body_lower = "they reached 1.7 million customers globally"
    assert claims_grounded_in_body(s_lower, body_lower) is True

    # Path B: capitalized "Million" in prose vs lowercase "million" in body.
    # Without IGNORECASE, the ``million`` alternation does not match capital
    # ``Million``, but the ``[MBK]`` character class still matches the bare
    # ``M``. findall therefore returns ``'1.7 M'`` (no trailing-word group --
    # next char ``illion`` has no leading whitespace). Body lacks ``'1.7 M'``
    # (its ``m`` is lowercase) -> False. Re-adding IGNORECASE would extract
    # ``'1.7 Million customers'`` and still miss -> behavior is the same here,
    # but the assertion documents the case-sensitive substring contract.
    s_mixed = _synth(why_similar="Reached 1.7 Million customers.")
    body_mixed = "they reached 1.7 million customers globally"
    assert claims_grounded_in_body(s_mixed, body_mixed) is False
