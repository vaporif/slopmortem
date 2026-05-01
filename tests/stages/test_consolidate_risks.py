"""Unit tests for ``slopmortem.stages.consolidate_risks``.

LLM I/O is mocked via :class:`FakeLLMClient`; the tests cover empty-input
short-circuit, happy path, severity cap demotion, fabricated-id pruning,
injection-flag handling, and per-candidate lesson dedup at the prompt
boundary.
"""

from __future__ import annotations

import json
from datetime import date
from typing import TYPE_CHECKING

import pytest

from conftest import llm_canned_key
from slopmortem.config import Config
from slopmortem.llm.fake import FakeLLMClient, FakeResponse
from slopmortem.llm.prompts import render_prompt
from slopmortem.models import (
    PerspectiveScore,
    SimilarityScores,
    Synthesis,
)
from slopmortem.stages import consolidate_risks as cr_module
from slopmortem.stages.consolidate_risks import consolidate_risks
from slopmortem.tracing.events import SpanEvent

if TYPE_CHECKING:
    from collections.abc import Mapping

    from slopmortem.llm.client import CompletionResult


_MODEL = "test-consolidate-model"
_PITCH = "A US consumer crypto savings platform paying yield on customer deposits."


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


def _canned_for(
    syntheses: list[Synthesis], payload: str
) -> Mapping[tuple[str, str, str], FakeResponse | CompletionResult]:
    """Render the prompt the stage will produce and pin the canned response."""
    prompt = render_prompt(
        "consolidate_risks",
        pitch=_PITCH,
        lessons=[
            {
                "candidate_id": s.candidate_id,
                "candidate_name": s.name,
                "lesson": lesson,
            }
            for s in syntheses
            for lesson in _dedup_lessons(s)
        ],
        candidate_ids=[s.candidate_id for s in syntheses],
    )
    return {
        llm_canned_key("consolidate_risks", model=_MODEL, prompt=prompt): FakeResponse(
            text=payload
        ),
    }


def _dedup_lessons(syn: Synthesis) -> list[str]:
    """Mirror the stage's per-candidate dedup so canned-prompt rendering matches."""
    seen: set[str] = set()
    out: list[str] = []
    for lesson in syn.lessons_for_input:
        key = lesson.strip().lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(lesson)
    return out


@pytest.fixture
def captured_events(monkeypatch: pytest.MonkeyPatch) -> list[SpanEvent]:
    events: list[SpanEvent] = []
    monkeypatch.setattr(cr_module, "_emit_event", events.append)
    return events


async def test_empty_syntheses_short_circuits_without_llm_call() -> None:
    fake_llm = FakeLLMClient(canned={}, default_model=_MODEL)
    result = await consolidate_risks(
        [],
        pitch=_PITCH,
        llm=fake_llm,
        config=Config(),
        model=_MODEL,
        max_tokens=2048,
    )
    assert result.risks == []
    assert result.injection_detected is False
    assert fake_llm.calls == []


async def test_happy_path_returns_consolidated_risks() -> None:
    syns = [
        _synthesis(
            candidate_id="ftx",
            name="FTX",
            lessons=["Segregate customer deposits."],
        ),
        _synthesis(
            candidate_id="celsius",
            name="Celsius",
            lessons=["Disclose lending counterparty exposure."],
        ),
    ]
    payload = json.dumps(
        {
            "top_risks": [
                {
                    "summary": "Segregate customer deposits in bankruptcy-remote custody.",
                    "applies_because": "Pitch already custodies customer crypto.",
                    "raised_by": ["ftx", "celsius"],
                    "severity": "high",
                },
                {
                    "summary": "Disclose lending counterparty exposure clearly.",
                    "applies_because": "Pitch lends to institutional borrowers.",
                    "raised_by": ["celsius"],
                    "severity": "medium",
                },
            ],
            "injection_detected": False,
        }
    )
    fake_llm = FakeLLMClient(canned=_canned_for(syns, payload), default_model=_MODEL)
    result = await consolidate_risks(
        syns,
        pitch=_PITCH,
        llm=fake_llm,
        config=Config(),
        model=_MODEL,
        max_tokens=2048,
    )

    assert len(result.risks) == 2
    assert [r.severity for r in result.risks] == ["high", "medium"]
    assert result.risks[0].raised_by == ["ftx", "celsius"]
    assert result.risks[0].applies_because.startswith("Pitch already")
    assert result.injection_detected is False


async def test_severity_cap_demotes_excess_highs() -> None:
    syns = [
        _synthesis(
            candidate_id=f"c{i}",
            name=f"C{i}",
            lessons=[f"lesson {i}"],
        )
        for i in range(6)
    ]
    # 6 highs from the LLM, with raised_by lengths 1..6 so the lowest-count
    # highs are the ones we expect to get demoted. First entry has 1 id; last
    # entry has 6 ids — strongest signal stays "high".
    top_risks = [
        {
            "summary": f"Risk {i}",
            "applies_because": f"because {i}",
            "raised_by": [f"c{j}" for j in range(i + 1)],
            "severity": "high",
        }
        for i in range(6)
    ]
    payload = json.dumps({"top_risks": top_risks, "injection_detected": False})
    fake_llm = FakeLLMClient(canned=_canned_for(syns, payload), default_model=_MODEL)

    result = await consolidate_risks(
        syns,
        pitch=_PITCH,
        llm=fake_llm,
        config=Config(),
        model=_MODEL,
        max_tokens=2048,
    )

    severities = [r.severity for r in result.risks]
    assert severities.count("high") == 4
    assert severities.count("medium") == 2
    # The two with the smallest raised_by counts should have been demoted.
    medium_risks = [r for r in result.risks if r.severity == "medium"]
    assert all(len(r.raised_by) <= 2 for r in medium_risks)


async def test_fabricated_candidate_id_is_dropped() -> None:
    syns = [
        _synthesis(
            candidate_id="real-co",
            name="RealCo",
            lessons=["Real lesson."],
        ),
    ]
    payload = json.dumps(
        {
            "top_risks": [
                {
                    "summary": "Fabricated risk.",
                    "applies_because": "irrelevant",
                    "raised_by": ["definitely-not-a-real-id"],
                    "severity": "high",
                },
                {
                    "summary": "Real risk.",
                    "applies_because": "Pitch matches RealCo's pattern.",
                    "raised_by": ["real-co"],
                    "severity": "medium",
                },
            ],
            "injection_detected": False,
        }
    )
    fake_llm = FakeLLMClient(canned=_canned_for(syns, payload), default_model=_MODEL)

    result = await consolidate_risks(
        syns,
        pitch=_PITCH,
        llm=fake_llm,
        config=Config(),
        model=_MODEL,
        max_tokens=2048,
    )

    summaries = [r.summary for r in result.risks]
    assert summaries == ["Real risk."]
    assert result.risks[0].raised_by == ["real-co"]


async def test_injection_detected_flag_sets_event_and_returns_empty(
    captured_events: list[SpanEvent],
) -> None:
    syns = [
        _synthesis(
            candidate_id="real-co",
            name="RealCo",
            lessons=["Lesson."],
        ),
    ]
    payload = json.dumps({"top_risks": [], "injection_detected": True})
    fake_llm = FakeLLMClient(canned=_canned_for(syns, payload), default_model=_MODEL)

    result = await consolidate_risks(
        syns,
        pitch=_PITCH,
        llm=fake_llm,
        config=Config(),
        model=_MODEL,
        max_tokens=2048,
    )

    assert result.risks == []
    assert result.injection_detected is True
    assert SpanEvent.PROMPT_INJECTION_ATTEMPTED in captured_events


async def test_lesson_dedup_within_same_candidate() -> None:
    """Identical lesson text from the same candidate must reach the prompt only once."""
    syns = [
        _synthesis(
            candidate_id="acme",
            name="Acme",
            lessons=["Be careful.", "Be careful.", "  Be careful.  "],
        ),
    ]
    payload = json.dumps({"top_risks": [], "injection_detected": False})
    fake_llm = FakeLLMClient(canned=_canned_for(syns, payload), default_model=_MODEL)

    _ = await consolidate_risks(
        syns,
        pitch=_PITCH,
        llm=fake_llm,
        config=Config(),
        model=_MODEL,
        max_tokens=2048,
    )

    assert len(fake_llm.calls) == 1
    rendered_prompt = fake_llm.calls[0].prompt
    # The lesson text should appear exactly once in the rendered prompt.
    assert rendered_prompt.count("Be careful.") == 1
