"""Tests for the ``synthesize`` stage: structured-output round trip + cache-warm pattern."""

from __future__ import annotations

import json
from datetime import date

from conftest import llm_canned_key
from slopmortem.config import Config
from slopmortem.llm.fake import FakeLLMClient, FakeResponse
from slopmortem.llm.prompts import render_prompt
from slopmortem.models import Candidate, CandidatePayload, Facets, InputContext
from slopmortem.stages.synthesize import synthesize, synthesize_all

_DEFAULT_MODEL = "test-synth-model"


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
    name: str = "Acme",
    body: str = "Acme was a B2B fintech that ran out of runway.",
    sources: list[str] | None = None,
) -> CandidatePayload:
    return CandidatePayload(
        name=name,
        summary="summary",
        body=body,
        facets=_facets(),
        founding_date=date(2018, 1, 1),
        failure_date=date(2023, 1, 1),
        founding_date_unknown=False,
        failure_date_unknown=False,
        provenance="curated_real",
        slop_score=0.0,
        sources=sources if sources is not None else ["https://acme.com/postmortem"],
        text_id="abcdef0123456789",
    )


def _candidate(
    *,
    canonical_id: str = "acme-corp",
    name: str = "Acme",
    body: str = "Acme was a B2B fintech that ran out of runway.",
    sources: list[str] | None = None,
) -> Candidate:
    return Candidate(
        canonical_id=canonical_id,
        score=0.9,
        payload=_payload(name=name, body=body, sources=sources),
    )


def _ctx() -> InputContext:
    return InputContext(name="newco", description="A B2B fintech for SMB invoicing")


def _synthesis_payload(
    *,
    candidate_id: str = "acme-corp",
    name: str = "Acme",
    where_diverged: str = "New pitch is web-first; Acme was mobile-only.",
    sources: list[str] | None = None,
) -> str:
    return json.dumps(
        {
            "candidate_id": candidate_id,
            "name": name,
            "one_liner": "B2B fintech for SMB invoicing.",
            "failure_date": "2023-01-01",
            "lifespan_months": 60,
            "similarity": {
                "business_model": {"score": 7.0, "rationale": "both B2B SaaS"},
                "market": {"score": 6.0, "rationale": "SMB invoicing overlap"},
                "gtm": {"score": 5.0, "rationale": "outbound sales"},
                "stage_scale": {"score": 4.0, "rationale": "seed stage"},
            },
            "why_similar": "Both target SMB invoicing with B2B SaaS pricing.",
            "where_diverged": where_diverged,
            "failure_causes": ["CAC > LTV", "long sales cycles"],
            "lessons_for_input": ["target larger ACVs", "avoid SMB churn traps"],
            "sources": sources if sources is not None else ["https://acme.com/postmortem"],
        }
    )


def _synthesize_canned(
    candidates: list[Candidate],
    ctx: InputContext,
    *,
    text: str,
    cache_creation_tokens: int | None = None,
    model: str = _DEFAULT_MODEL,
) -> dict[tuple[str, str, str], FakeResponse]:
    """Build one canned entry per rendered synthesize prompt for ``candidates``."""
    out: dict[tuple[str, str, str], FakeResponse] = {}
    for cand in candidates:
        rendered = render_prompt(
            "synthesize",
            pitch=ctx.description,
            candidate_id=cand.canonical_id,
            candidate_name=cand.payload.name,
            candidate_body=cand.payload.body,
        )
        out[llm_canned_key("synthesize", model=model, prompt=rendered)] = FakeResponse(
            text=text, cache_creation_tokens=cache_creation_tokens
        )
    return out


async def test_synthesize_returns_filled_synthesis() -> None:
    cand = _candidate()
    payload = _synthesis_payload(candidate_id=cand.canonical_id)
    fake_llm = FakeLLMClient(
        canned=_synthesize_canned([cand], _ctx(), text=payload),
        default_model=_DEFAULT_MODEL,
    )

    s = await synthesize(cand, _ctx(), fake_llm, Config(), model=_DEFAULT_MODEL)

    # Anti-cheerleading guard: the prompt forces a non-empty divergence note.
    assert s.where_diverged.strip() != ""
    assert s.candidate_id == cand.canonical_id
    assert s.similarity.business_model.score >= 0


async def test_synthesize_all_warms_cache_before_gather() -> None:
    """First call asserted as cache-warm; remaining run via asyncio.gather."""
    cands = [_candidate(canonical_id=f"cand-{i}") for i in range(3)]
    payload = _synthesis_payload(candidate_id="cand-0")
    # One canned entry per rendered (template_sha, model, prompt_hash); the
    # 3-tuple lookup is strict so each candidate's prompt needs its own key.
    fake_llm = FakeLLMClient(
        canned=_synthesize_canned(cands, _ctx(), text=payload, cache_creation_tokens=10),
        default_model=_DEFAULT_MODEL,
    )

    results = await synthesize_all(cands, _ctx(), fake_llm, Config(), model=_DEFAULT_MODEL)

    assert len(results) == len(cands)
    # First call must have observed cache-creation tokens (warm path).
    assert fake_llm.calls[0].cache is True
    # No exceptions in the canned-response happy path.
    assert all(not isinstance(r, Exception) for r in results)
