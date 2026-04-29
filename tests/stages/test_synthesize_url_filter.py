"""URL allowlist filter test for synthesize: drops sources whose host isn't in the allowlist."""

import json
from datetime import date
from urllib.parse import urlparse

from conftest import llm_canned_key
from slopmortem.config import Config
from slopmortem.llm.fake import FakeLLMClient, FakeResponse
from slopmortem.llm.prompts import render_prompt
from slopmortem.models import Candidate, CandidatePayload, Facets, InputContext
from slopmortem.stages.synthesize import synthesize

_DEFAULT_MODEL = "test-synth-model"


def _candidate() -> Candidate:
    facets = Facets(
        sector="fintech",
        business_model="b2b_saas",
        customer_type="smb",
        geography="us",
        monetization="subscription_recurring",
    )
    payload = CandidatePayload(
        name="Acme",
        summary="summary",
        body="Acme was a B2B fintech that ran out of runway.",
        facets=facets,
        founding_date=date(2018, 1, 1),
        failure_date=date(2023, 1, 1),
        founding_date_unknown=False,
        failure_date_unknown=False,
        provenance="curated_real",
        slop_score=0.0,
        sources=["https://acme.com/postmortem"],
        text_id="abcdef0123456789",
    )
    return Candidate(canonical_id="acme-corp", score=0.9, payload=payload)


def _ctx() -> InputContext:
    return InputContext(name="newco", description="A B2B fintech for SMB invoicing")


def _bad_synthesis_payload() -> str:
    """Synthesis JSON whose `sources` mixes an allowed acme.com URL with attacker.com."""
    return json.dumps(
        {
            "candidate_id": "acme-corp",
            "name": "Acme",
            "one_liner": "B2B fintech for SMB invoicing.",
            "failure_date": "2023-01-01",
            "lifespan_months": 60,
            "similarity": {
                "business_model": {"score": 7.0, "rationale": "both B2B SaaS"},
                "market": {"score": 6.0, "rationale": "SMB invoicing overlap"},
                "gtm": {"score": 5.0, "rationale": "outbound sales"},
                "stage_scale": {"score": 4.0, "rationale": "seed stage"},
            },
            "why_similar": "Both target SMB invoicing.",
            "where_diverged": "New pitch is web-first.",
            "failure_causes": ["CAC > LTV"],
            "lessons_for_input": ["target larger ACVs"],
            "sources": [
                "https://acme.com/postmortem",
                "https://attacker.com/x",
                "https://news.ycombinator.com/item?id=123",
                "https://malicious.example/leak",
            ],
        }
    )


async def test_synthesize_drops_off_allowlist_urls() -> None:
    cand = _candidate()
    rendered = render_prompt(
        "synthesize",
        pitch=_ctx().description,
        candidate_id=cand.canonical_id,
        candidate_name=cand.payload.name,
        candidate_body=cand.payload.body,
    )
    fake_llm = FakeLLMClient(
        canned={
            llm_canned_key("synthesize", model=_DEFAULT_MODEL, prompt=rendered): FakeResponse(
                text=_bad_synthesis_payload()
            ),
        },
        default_model=_DEFAULT_MODEL,
    )

    s = await synthesize(cand, _ctx(), fake_llm, Config(), model=_DEFAULT_MODEL)

    # Allowed: every host in candidate.payload.sources, plus the fixed allowlist.
    allowed_hosts = {"acme.com", "news.ycombinator.com"}
    for url in s.sources:
        host = urlparse(url).hostname
        assert host in allowed_hosts, f"leaked off-allowlist url: {url}"

    # The clean URLs survive; attacker.com and malicious.example are gone.
    assert "https://acme.com/postmortem" in s.sources
    assert any("news.ycombinator.com" in u for u in s.sources)
    assert not any("attacker.com" in u for u in s.sources)
    assert not any("malicious.example" in u for u in s.sources)
