"""Consolidate-risks stage: LLM-driven applicability filter and paraphrase merge.

Replaces the deterministic Jaccard cluster pass. One sonnet call sees the pitch
plus every per-candidate lesson and returns up to 10 risks that genuinely apply
to the pitch, each with a canonical summary, the comparables that raised it,
an `applies_because` line that quotes a concrete pitch element, and a severity
bucket.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lmnr import Laminar

from slopmortem.llm.prompts import prompt_template_sha, render_prompt
from slopmortem.llm.tools import to_strict_response_schema
from slopmortem.models import (
    ConsolidatedRisk,
    LLMTopRisksConsolidation,
    TopRisks,
)
from slopmortem.tracing.events import SpanEvent

if TYPE_CHECKING:
    from slopmortem.config import Config
    from slopmortem.llm.client import LLMClient
    from slopmortem.models import Synthesis


_MAX_HIGHS = 4
_SEVERITY_RANK: dict[str, int] = {"high": 0, "medium": 1, "low": 2}


def _emit_event(event: SpanEvent) -> None:
    """Emit event as a Laminar span event when tracing is initialized."""
    if Laminar.is_initialized():
        Laminar.event(name=str(event))


async def consolidate_risks(  # noqa: PLR0913 — every dep is required wiring at the call site
    syntheses: list[Synthesis],
    *,
    pitch: str,
    llm: LLMClient,
    config: Config,  # noqa: ARG001 — reserved for future per-stage knobs (mirrors synthesize)
    model: str,
    max_tokens: int,
) -> TopRisks:
    """LLM-driven applicability filter + paraphrase merge over per-candidate lessons.

    Returns an empty :class:`TopRisks` when ``syntheses`` is empty.
    """
    if not syntheses:
        return TopRisks()

    candidate_ids = [s.candidate_id for s in syntheses]
    valid_ids = set(candidate_ids)

    seen: set[tuple[str, str]] = set()
    lessons: list[dict[str, str]] = []
    for syn in syntheses:
        for lesson in syn.lessons_for_input:
            key = (syn.candidate_id, lesson.strip().lower())
            if key in seen:
                continue
            seen.add(key)
            lessons.append(
                {
                    "candidate_id": syn.candidate_id,
                    "candidate_name": syn.name,
                    "lesson": lesson,
                }
            )

    prompt = render_prompt(
        "consolidate_risks",
        pitch=pitch,
        lessons=lessons,
        candidate_ids=candidate_ids,
    )
    result = await llm.complete(
        prompt,
        model=model,
        cache=True,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "TopRisksConsolidation",
                "schema": to_strict_response_schema(LLMTopRisksConsolidation),
                "strict": True,
            },
        },
        extra_body={
            "provider": {"require_parameters": True},
            "prompt_template_sha": prompt_template_sha("consolidate_risks"),
        },
        max_tokens=max_tokens,
    )
    parsed = LLMTopRisksConsolidation.model_validate_json(result.text)

    if parsed.injection_detected:
        _emit_event(SpanEvent.PROMPT_INJECTION_ATTEMPTED)
        return TopRisks(risks=[], injection_detected=True)

    risks: list[ConsolidatedRisk] = []
    for entry in parsed.top_risks:
        cleaned_ids = [cid for cid in entry.raised_by if cid in valid_ids]
        if not cleaned_ids:
            continue
        risks.append(
            ConsolidatedRisk(
                summary=entry.summary,
                applies_because=entry.applies_because,
                raised_by=cleaned_ids,
                severity=entry.severity,
            )
        )

    # Defense-in-depth severity cap: prompt also says max 4 highs. If the LLM
    # over-emits, demote the lowest-`raised_by`-count highs to "medium".
    high_indices = sorted(
        (i for i, r in enumerate(risks) if r.severity == "high"),
        key=lambda i: len(risks[i].raised_by),
    )
    demote = set(high_indices[: max(0, len(high_indices) - _MAX_HIGHS)])
    if demote:
        risks = [
            r.model_copy(update={"severity": "medium"}) if i in demote else r
            for i, r in enumerate(risks)
        ]

    risks.sort(key=lambda r: (_SEVERITY_RANK[r.severity], -len(r.raised_by)))

    return TopRisks(risks=risks, injection_detected=False)
