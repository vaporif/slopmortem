"""End-to-end propagation: synthesize marker -> consolidate short-circuits.

Closes the contract gap CLAUDE.md describes as load-bearing: when synthesize
detects ``_INJECTION_MARKER``, ``consolidate_risks`` must return empty
``TopRisks`` without consulting the consolidator LLM.
"""

from __future__ import annotations

import importlib
from datetime import date

import pytest

from slopmortem.config import Config
from slopmortem.llm import FakeLLMClient, NoCannedResponseError
from slopmortem.models import (
    PerspectiveScore,
    SimilarityScores,
    Synthesis,
    TopRisks,
)
from slopmortem.stages import consolidate_risks
from slopmortem.tracing import SpanEvent

# `import_module` instead of `import x as` — the façade re-export shadows the submodule.
_cr_module = importlib.import_module("slopmortem.stages.consolidate_risks")

_MODEL = "test-consolidate-model"
_PITCH = "A US consumer crypto savings platform paying yield on customer deposits."


def _scores(value: float = 5.0) -> SimilarityScores:
    return SimilarityScores(
        business_model=PerspectiveScore(score=value, rationale="bm"),
        market=PerspectiveScore(score=value, rationale="market"),
        gtm=PerspectiveScore(score=value, rationale="gtm"),
        stage_scale=PerspectiveScore(score=value, rationale="stage"),
    )


def _synthesis(
    *,
    candidate_id: str,
    name: str,
    lessons: list[str],
    injection_detected: bool = False,
) -> Synthesis:
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
        injection_detected=injection_detected,
    )


@pytest.fixture
def captured_events(monkeypatch: pytest.MonkeyPatch) -> list[SpanEvent]:
    events: list[SpanEvent] = []
    monkeypatch.setattr(_cr_module, "_emit_event", events.append)
    return events


async def test_all_clean_runs_consolidator(
    captured_events: list[SpanEvent],
) -> None:
    """Regression: when no synthesis is tainted, the consolidator LLM still runs."""
    syns = [
        _synthesis(candidate_id="c1", name="Co1", lessons=["lesson 1"]),
        _synthesis(candidate_id="c2", name="Co2", lessons=["lesson 2"]),
    ]
    # Empty canned dict: the consolidator runs but raises NoCannedResponseError.
    # We assert on that raise rather than re-rendering a real prompt key.
    fake_llm = FakeLLMClient(canned={}, default_model=_MODEL)
    with pytest.raises(NoCannedResponseError):
        _ = await consolidate_risks(
            syns,
            pitch=_PITCH,
            llm=fake_llm,
            config=Config(),
            model=_MODEL,
            max_tokens=512,
        )
    assert len(fake_llm.calls) == 1
    assert SpanEvent.PROMPT_INJECTION_ATTEMPTED not in captured_events


async def test_one_tainted_synthesis_short_circuits(
    captured_events: list[SpanEvent],
) -> None:
    tainted = _synthesis(candidate_id="evil", name="Evil", lessons=["bad"], injection_detected=True)
    clean = _synthesis(candidate_id="good", name="Good", lessons=["fine"])
    fake_llm = FakeLLMClient(canned={}, default_model=_MODEL)

    result = await consolidate_risks(
        [tainted, clean],
        pitch=_PITCH,
        llm=fake_llm,
        config=Config(),
        model=_MODEL,
        max_tokens=512,
    )

    assert result == TopRisks(risks=[], injection_detected=True)
    assert len(fake_llm.calls) == 0
    assert captured_events == [SpanEvent.PROMPT_INJECTION_ATTEMPTED]


async def test_all_tainted_short_circuits(
    captured_events: list[SpanEvent],
) -> None:
    syns = [
        _synthesis(
            candidate_id=f"c{i}",
            name=f"Co{i}",
            lessons=[f"l{i}"],
            injection_detected=True,
        )
        for i in range(3)
    ]
    fake_llm = FakeLLMClient(canned={}, default_model=_MODEL)

    result = await consolidate_risks(
        syns,
        pitch=_PITCH,
        llm=fake_llm,
        config=Config(),
        model=_MODEL,
        max_tokens=512,
    )

    assert result == TopRisks(risks=[], injection_detected=True)
    assert len(fake_llm.calls) == 0
    assert captured_events == [SpanEvent.PROMPT_INJECTION_ATTEMPTED]
