"""Tests for the ``llm_rerank`` stage: structured-output round trip + length guard."""

from __future__ import annotations

import json
from datetime import date

import pytest

from conftest import llm_canned_key
from slopmortem.config import Config
from slopmortem.errors import RerankLengthError
from slopmortem.llm.fake import FakeLLMClient, FakeResponse
from slopmortem.llm.prompts import render_prompt
from slopmortem.models import Candidate, CandidatePayload, Facets
from slopmortem.stages.llm_rerank import llm_rerank

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
    # pinned to repo-root ``slopmortem.toml`` which doesn't override these.
    cfg = Config()
    return cfg.model_copy(update={"N_synthesize": n_synthesize})


def _rerank_canned(
    *,
    text: str,
    pitch: str,
    candidates: list[Candidate],
    model: str = _DEFAULT_MODEL,
) -> dict[tuple[str, str, str], FakeResponse]:
    rendered = render_prompt(
        "llm_rerank",
        pitch=pitch,
        facets=_facets().model_dump(),
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
        canned=_rerank_canned(text=payload, pitch="pitch text", candidates=candidates),
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
        canned=_rerank_canned(text=payload, pitch="pitch", candidates=candidates),
        default_model=_DEFAULT_MODEL,
    )

    _ = await llm_rerank(candidates, "pitch", _facets(), fake_llm, cfg)

    sent = fake_llm.calls[0].prompt
    assert "THIS_SUMMARY" in sent
    assert "DO_NOT_INCLUDE_BODY" not in sent


async def test_llm_rerank_raises_on_length_mismatch() -> None:
    candidates = _make_candidates(30)
    cfg = _config(n_synthesize=5)
    # Return only 3 ranked entries — strict-mode JSON schema doesn't constrain
    # array length, so the stage's post-parse length guard must trigger.
    payload = _scored_payload([c.canonical_id for c in candidates[:3]])
    fake_llm = FakeLLMClient(
        canned=_rerank_canned(text=payload, pitch="pitch", candidates=candidates),
        default_model=_DEFAULT_MODEL,
    )

    with pytest.raises(RerankLengthError, match="expected 5, got 3"):
        await llm_rerank(candidates, "pitch", _facets(), fake_llm, cfg)
