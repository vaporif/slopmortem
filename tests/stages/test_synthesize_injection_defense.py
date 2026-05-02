"""Injection-defense for synthesize: emits PROMPT_INJECTION_ATTEMPTED and drops attacker URLs."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from conftest import llm_canned_key
from slopmortem.config import Config
from slopmortem.llm import FakeLLMClient, FakeResponse, render_prompt
from slopmortem.models import Candidate, CandidatePayload, Facets, InputContext
from slopmortem.stages import synthesize, synthesize_prompt_kwargs
from slopmortem.stages import synthesize as synth_module
from slopmortem.tracing.events import SpanEvent

_DEFAULT_MODEL = "test-synth-model"
_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "injection"


def _load_injection_fixtures() -> list[tuple[str, str]]:
    """Read every ``.txt`` in the injection fixture dir at collection time.

    Returns ``(stem, body)`` pairs so parametrize ids stay readable and the
    test body never touches the filesystem (avoids ``ASYNC240``).
    """
    return [(p.stem, p.read_text()) for p in sorted(_FIXTURE_DIR.glob("*.txt"))]


def _candidate(*, body: str) -> Candidate:
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
        body=body,
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


def _injection_synthesis_payload() -> str:
    """Synthesis JSON the LLM emits when it detects an injection attempt.

    The prompt template tells the LLM to write the literal string
    ``prompt_injection_attempted`` into ``where_diverged`` when an
    injection is detected. Sources are passed through from the candidate
    payload by the synthesize stage, so they aren't part of the LLM contract.
    """
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
            "where_diverged": "prompt_injection_attempted",
            "failure_causes": ["CAC > LTV"],
            "lessons_for_input": ["target larger ACVs"],
        }
    )


@pytest.fixture
def captured_events(monkeypatch: pytest.MonkeyPatch) -> list[SpanEvent]:
    """Replace the no-op ``_emit_event`` with a list-appending stub for one test."""
    events: list[SpanEvent] = []
    monkeypatch.setattr(synth_module, "_emit_event", events.append)
    return events


_INJECTION_FIXTURES = _load_injection_fixtures()


@pytest.mark.parametrize(
    ("fixture_name", "body"),
    _INJECTION_FIXTURES,
    ids=[name for name, _ in _INJECTION_FIXTURES],
)
async def test_synthesize_ignores_injected_instructions(
    fixture_name: str,
    body: str,
    captured_events: list[SpanEvent],
) -> None:
    del fixture_name  # only used for parametrize id generation
    cand = _candidate(body=body)
    rendered = render_prompt(
        "synthesize", **synthesize_prompt_kwargs(cand, pitch=_ctx().description)
    )
    fake_llm = FakeLLMClient(
        canned={
            llm_canned_key("synthesize", model=_DEFAULT_MODEL, prompt=rendered): FakeResponse(
                text=_injection_synthesis_payload()
            ),
        },
        default_model=_DEFAULT_MODEL,
    )

    s = await synthesize(cand, _ctx(), fake_llm, Config(), model=_DEFAULT_MODEL)

    # Sources are sourced from CandidatePayload, not the LLM, so the only
    # thing we need to confirm is that the injection signal was emitted.
    assert s.sources == cand.payload.sources
    assert SpanEvent.PROMPT_INJECTION_ATTEMPTED in captured_events
    assert s.injection_detected is True
