"""Tests for the ``llm_rerank`` stage: structured-output round trip and length guard."""

from __future__ import annotations

import json
from datetime import date

import pytest

from conftest import llm_canned_key
from slopmortem.config import Config
from slopmortem.errors import RerankLengthError
from slopmortem.llm import FakeLLMClient, FakeResponse, render_prompt
from slopmortem.models import Candidate, CandidatePayload, Facets
from slopmortem.stages import llm_rerank

_DEFAULT_MODEL = "test-rerank-model"


def _facets() -> Facets:
    return Facets(
        sector="fintech",
        business_model="b2b_saas",
        customer_type="smb",
        geography="us",
        monetization="subscription_recurring",
    )


def _payload(
    *,
    name: str,
    summary: str = "summary text",
    body: str = "body text",
) -> CandidatePayload:
    return CandidatePayload(
        name=name,
        summary=summary,
        body=body,
        facets=_facets(),
        founding_date=date(2018, 1, 1),
        failure_date=date(2023, 1, 1),
        founding_date_unknown=False,
        failure_date_unknown=False,
        provenance="curated_real",
        slop_score=0.0,
        sources=["curated:0"],
        text_id="abcdef0123456789",
    )


def _make_candidates(n: int) -> list[Candidate]:
    return [
        Candidate(
            canonical_id=f"cand-{i}",
            score=1.0 - (i * 0.01),
            payload=_payload(name=f"Cand {i}"),
        )
        for i in range(n)
    ]


def _scored_payload(candidate_ids: list[str]) -> str:
    """Render a strict-mode-shaped LlmRerankResult JSON for the given ids."""
    ranked = [
        {
            "candidate_id": cid,
            "perspective_scores": {
                "business_model": {"score": 7.0, "rationale": "matches"},
                "market": {"score": 6.0, "rationale": "ok"},
                "gtm": {"score": 5.0, "rationale": "ok"},
                "stage_scale": {"score": 4.0, "rationale": "ok"},
            },
            "rationale": "ranked",
        }
        for cid in candidate_ids
    ]
    return json.dumps({"ranked": ranked})


def _config(*, n_synthesize: int = 5) -> Config:
    # ``Config`` defaults every knob; we override only ``N_synthesize`` per
    # test. ``BaseSettings`` also reads env/TOML, but the test suite is
    # pinned to repo-root ``slopmortem.toml`` which does not override these.
    cfg = Config()
    return cfg.model_copy(update={"N_synthesize": n_synthesize})


def _rerank_canned(
    *,
    text: str,
    pitch: str,
    candidates: list[Candidate],
    top_n: int,
    model: str = _DEFAULT_MODEL,
) -> dict[tuple[str, str, str], FakeResponse]:
    rendered = render_prompt(
        "llm_rerank",
        pitch=pitch,
        facets=_facets().model_dump(),
        top_n=top_n,
        candidates=[
            {
                "candidate_id": c.canonical_id,
                "name": c.payload.name,
                "summary": c.payload.summary,
            }
            for c in candidates
        ],
    )
    return {
        llm_canned_key("llm_rerank", model=model, prompt=rendered): FakeResponse(text=text),
    }


async def test_llm_rerank_returns_n_synthesize() -> None:
    candidates = _make_candidates(30)
    cfg = _config(n_synthesize=5)
    payload = _scored_payload([c.canonical_id for c in candidates[:5]])
    fake_llm = FakeLLMClient(
        canned=_rerank_canned(
            text=payload, pitch="pitch text", candidates=candidates, top_n=cfg.N_synthesize
        ),
        default_model=_DEFAULT_MODEL,
    )

    result = await llm_rerank(candidates, "pitch text", _facets(), fake_llm, cfg)

    assert len(result.ranked) == 5
    assert all(isinstance(s.perspective_scores.business_model.score, float) for s in result.ranked)


async def test_llm_rerank_uses_summary_not_body() -> None:
    candidates = [
        Candidate(
            canonical_id="only-cand",
            score=1.0,
            payload=_payload(
                name="Only",
                summary="THIS_SUMMARY",
                body="DO_NOT_INCLUDE_BODY",
            ),
        )
    ]
    cfg = _config(n_synthesize=1)
    payload = _scored_payload(["only-cand"])
    fake_llm = FakeLLMClient(
        canned=_rerank_canned(
            text=payload, pitch="pitch", candidates=candidates, top_n=cfg.N_synthesize
        ),
        default_model=_DEFAULT_MODEL,
    )

    _ = await llm_rerank(candidates, "pitch", _facets(), fake_llm, cfg)

    sent = fake_llm.calls[0].prompt
    assert "THIS_SUMMARY" in sent
    assert "DO_NOT_INCLUDE_BODY" not in sent


async def test_llm_rerank_raises_on_length_mismatch() -> None:
    candidates = _make_candidates(30)
    cfg = _config(n_synthesize=5)
    # Return only 3 ranked entries. Strict-mode JSON schema does not constrain
    # array length, so the stage's post-parse length guard must trigger.
    payload = _scored_payload([c.canonical_id for c in candidates[:3]])
    fake_llm = FakeLLMClient(
        canned=_rerank_canned(
            text=payload, pitch="pitch", candidates=candidates, top_n=cfg.N_synthesize
        ),
        default_model=_DEFAULT_MODEL,
    )

    with pytest.raises(RerankLengthError, match="expected 5, got 3"):
        await llm_rerank(candidates, "pitch", _facets(), fake_llm, cfg)


async def test_llm_rerank_accepts_short_when_candidates_below_n() -> None:
    candidates = _make_candidates(3)
    cfg = _config(n_synthesize=5)
    payload = _scored_payload([c.canonical_id for c in candidates])
    fake_llm = FakeLLMClient(
        canned=_rerank_canned(
            text=payload, pitch="pitch", candidates=candidates, top_n=cfg.N_synthesize
        ),
        default_model=_DEFAULT_MODEL,
    )

    result = await llm_rerank(candidates, "pitch", _facets(), fake_llm, cfg)

    assert len(result.ranked) == 3


async def test_llm_rerank_raises_when_llm_exceeds_top_n() -> None:
    candidates = _make_candidates(30)
    cfg = _config(n_synthesize=5)
    payload = _scored_payload([c.canonical_id for c in candidates[:7]])
    fake_llm = FakeLLMClient(
        canned=_rerank_canned(
            text=payload, pitch="pitch", candidates=candidates, top_n=cfg.N_synthesize
        ),
        default_model=_DEFAULT_MODEL,
    )

    with pytest.raises(RerankLengthError, match="expected 5, got 7"):
        await llm_rerank(candidates, "pitch", _facets(), fake_llm, cfg)


def _scored_with(cid: str, *, bm: float, mk: float, gtm: float, ss: float):
    from slopmortem.models import (  # noqa: PLC0415
        PerspectiveScore,
        ScoredCandidate,
        SimilarityScores,
    )

    return ScoredCandidate(
        candidate_id=cid,
        perspective_scores=SimilarityScores(
            business_model=PerspectiveScore(score=bm, rationale="x"),
            market=PerspectiveScore(score=mk, rationale="x"),
            gtm=PerspectiveScore(score=gtm, rationale="x"),
            stage_scale=PerspectiveScore(score=ss, rationale="x"),
        ),
        rationale="r",
    )


def _retrieved_candidate(canonical_id: str) -> Candidate:
    return Candidate(
        canonical_id=canonical_id,
        score=0.9,
        payload=CandidatePayload(
            name=canonical_id,
            summary=f"{canonical_id} summary",
            body=f"{canonical_id} body",
            facets=_facets(),
            founding_date=date(2018, 1, 1),
            failure_date=date(2023, 1, 1),
            founding_date_unknown=False,
            failure_date_unknown=False,
            provenance="curated_real",
            slop_score=0.0,
            sources=[],
            text_id=canonical_id.replace("-", "") + "0123456789",
        ),
    )


def test_join_by_id_preserves_rerank_order() -> None:
    from slopmortem.stages.llm_rerank import _join_by_id  # noqa: PLC0415

    retrieved = [_retrieved_candidate(f"cand-{i}") for i in range(5)]
    ranked = [
        _scored_with("cand-3", bm=1.0, mk=1.0, gtm=1.0, ss=1.0),
        _scored_with("cand-0", bm=1.0, mk=1.0, gtm=1.0, ss=1.0),
        _scored_with("cand-2", bm=1.0, mk=1.0, gtm=1.0, ss=1.0),
    ]
    joined = _join_by_id(retrieved, ranked)
    assert [c.canonical_id for c in joined] == ["cand-3", "cand-0", "cand-2"]


def test_join_by_id_drops_unknown_ids() -> None:
    from slopmortem.stages.llm_rerank import _join_by_id  # noqa: PLC0415

    retrieved = [_retrieved_candidate("cand-0"), _retrieved_candidate("cand-1")]
    ranked = [_scored_with("ghost", bm=1.0, mk=1.0, gtm=1.0, ss=1.0)]
    assert _join_by_id(retrieved, ranked) == []


def test_select_top_n_by_similarity_drops_below_threshold() -> None:
    from slopmortem.stages.llm_rerank import select_top_n_by_similarity  # noqa: PLC0415

    retrieved = [_retrieved_candidate("strong"), _retrieved_candidate("weak")]
    ranked = [
        _scored_with("strong", bm=7.0, mk=6.0, gtm=5.0, ss=4.0),  # mean = 5.5
        _scored_with("weak", bm=2.0, mk=2.0, gtm=2.0, ss=2.0),  # mean = 2.0
    ]
    top_n, dropped = select_top_n_by_similarity(
        retrieved=retrieved, ranked=ranked, min_similarity=4.0, n_synthesize=2
    )
    assert [c.canonical_id for c in top_n] == ["strong"]
    assert dropped == 1  # n_synthesize - len(top_n) == 2 - 1


def test_select_top_n_by_similarity_preserves_rerank_order() -> None:
    from slopmortem.stages.llm_rerank import select_top_n_by_similarity  # noqa: PLC0415

    retrieved = [_retrieved_candidate(cid) for cid in ("a", "b", "c")]
    ranked = [
        _scored_with("c", bm=5.0, mk=5.0, gtm=5.0, ss=5.0),
        _scored_with("a", bm=8.0, mk=8.0, gtm=8.0, ss=8.0),
        _scored_with("b", bm=6.0, mk=6.0, gtm=6.0, ss=6.0),
    ]
    top_n, _ = select_top_n_by_similarity(
        retrieved=retrieved, ranked=ranked, min_similarity=4.0, n_synthesize=3
    )
    assert [c.canonical_id for c in top_n] == ["c", "a", "b"]


def test_select_top_n_by_similarity_empty_when_all_below() -> None:
    from slopmortem.stages.llm_rerank import select_top_n_by_similarity  # noqa: PLC0415

    retrieved = [_retrieved_candidate("c1"), _retrieved_candidate("c2")]
    ranked = [
        _scored_with("c1", bm=2.0, mk=2.0, gtm=2.0, ss=4.0),  # mean = 2.5
        _scored_with("c2", bm=1.0, mk=1.0, gtm=1.0, ss=2.0),  # mean = 1.25
    ]
    top_n, dropped = select_top_n_by_similarity(
        retrieved=retrieved, ranked=ranked, min_similarity=4.0, n_synthesize=2
    )
    assert top_n == []
    assert dropped == 2
