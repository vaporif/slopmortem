"""Synthesize stage: per-candidate post-mortem generation with cache-warm fan-out.

One candidate per ``synthesize`` call. ``synthesize_all`` runs the cache-warm
pattern (first call alone, then :func:`gather_resilient` on the rest) so one
candidate's failure can't cancel the others.

The OpenRouter client (``slopmortem/llm/openrouter.py``) drives the tool-call
loop, wraps tool results in ``<untrusted_document>`` tags, and enforces the
5-turn bound. This stage is one ``llm.complete(...)`` call.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lmnr import Laminar

from slopmortem.concurrency import gather_resilient
from slopmortem.llm.prompts import prompt_template_sha, render_prompt
from slopmortem.llm.tools import synthesis_tools, to_strict_response_schema
from slopmortem.models import LLMSynthesis, Synthesis
from slopmortem.tracing.events import SpanEvent

if TYPE_CHECKING:
    from collections.abc import Callable

    from slopmortem.config import Config
    from slopmortem.llm.client import LLMClient
    from slopmortem.models import Candidate, InputContext


def synthesize_prompt_kwargs(candidate: Candidate, *, pitch: str) -> dict[str, Any]:  # pyright: ignore[reportExplicitAny]
    """Build the ``render_prompt("synthesize", ...)`` kwargs for *candidate*.

    Shared by the production stage and tests so both stay in lockstep when
    the template's variable list changes.
    """
    payload = candidate.payload
    facets = payload.facets
    return {
        "pitch": pitch,
        "candidate_id": candidate.canonical_id,
        "candidate_name": payload.name,
        "candidate_body": payload.body,
        "founding_date": payload.founding_date.isoformat() if payload.founding_date else None,
        "failure_date": payload.failure_date.isoformat() if payload.failure_date else None,
        "sub_sector": facets.sub_sector,
        "customer_type": facets.customer_type,
        "geography": facets.geography,
        "monetization": facets.monetization,
        "product_type": facets.product_type,
        "price_point": facets.price_point,
    }


# Literal contract from synthesize.j2: the LLM must put this exact string in
# ``where_diverged`` when it detects an injection attempt.
_INJECTION_MARKER = "prompt_injection_attempted"


def _emit_event(event: SpanEvent) -> None:
    """Emit event as a Laminar span event when tracing is initialized."""
    if Laminar.is_initialized():
        Laminar.event(name=str(event))


async def synthesize(  # noqa: PLR0913 — every dependency is required at the call site
    candidate: Candidate,
    ctx: InputContext,
    llm: LLMClient,
    config: Config,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
) -> Synthesis:
    """Generate one :class:`Synthesis` for *candidate* against *ctx*'s pitch.

    Args:
        candidate: One :class:`Candidate` from the rerank top-N. Its
            ``payload.body`` is inlined into the prompt inside
            ``<untrusted_document>`` tags.
        ctx: The user's :class:`InputContext`; ``ctx.description`` is the pitch.
        llm: Async :class:`LLMClient`. ``cache=True`` so the system block hits
            the prompt cache across calls within the 5-min TTL.
        config: :class:`Config`. Drives ``synthesis_tools`` (Tavily inclusion)
            and reserved for future per-stage knobs.
        model: Optional model override. ``None`` lets the client pick.
        max_tokens: Optional cap on completion tokens. ``None`` keeps the
            client default (no cap sent upstream).

    Returns:
        Parsed :class:`Synthesis`. ``sources`` is passed through directly from
        ``candidate.payload.sources`` (the LLM never sees provenance URLs, so
        asking it to cite them produced empty or hallucinated lists). When the
        LLM marks ``where_diverged == "prompt_injection_attempted"``,
        ``_emit_event`` fires :data:`SpanEvent.PROMPT_INJECTION_ATTEMPTED`.

    Raises:
        ValidationError: The LLM emitted JSON that doesn't validate against
            :class:`Synthesis`.
    """
    prompt = render_prompt(
        "synthesize",
        **synthesize_prompt_kwargs(candidate, pitch=ctx.description),
    )
    result = await llm.complete(
        prompt,
        model=model,
        cache=True,
        tools=synthesis_tools(config),
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "LLMSynthesis",
                "schema": to_strict_response_schema(LLMSynthesis),
                "strict": True,
            },
        },
        extra_body={
            "provider": {"require_parameters": True},
            "prompt_template_sha": prompt_template_sha("synthesize"),
        },
        max_tokens=max_tokens,
    )
    llm_parsed = LLMSynthesis.model_validate_json(result.text)

    injection_detected = llm_parsed.where_diverged.strip() == _INJECTION_MARKER
    if injection_detected:
        _emit_event(SpanEvent.PROMPT_INJECTION_ATTEMPTED)

    return Synthesis.from_llm(
        llm_parsed,
        founding_date=candidate.payload.founding_date,
        failure_date=candidate.payload.failure_date,
        sources=candidate.payload.sources,
        injection_detected=injection_detected,
    )


async def synthesize_all(  # noqa: PLR0913 — mirrors ``synthesize`` for the fan-out wrapper
    candidates: list[Candidate],
    ctx: InputContext,
    llm: LLMClient,
    config: Config,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    on_candidate_done: Callable[[BaseException | None], None] | None = None,
) -> list[Synthesis | BaseException]:
    """Cache-warm synthesize fan-out: one warm call, then :func:`gather_resilient`.

    The first call runs alone so the prompt cache is populated before the
    parallel fan-out hits it — avoids a pile-up of cache-write races. The rest
    run via :func:`gather_resilient` so one failed candidate can't cancel its
    siblings; the reporting path filters out exceptions and notes the gap on
    ``Report.candidates``.

    Args:
        candidates: All candidates to synthesize. May be empty.
        ctx: The user's :class:`InputContext`.
        llm: Async :class:`LLMClient`.
        config: :class:`Config`.
        model: Optional model override.
        max_tokens: Optional cap on completion tokens, forwarded to each
            :func:`synthesize` call.
        on_candidate_done: Optional callback fired exactly once per candidate
            when its ``synthesize`` call settles. Receives ``None`` on success
            or the raised :class:`BaseException` on failure. For CLI
            progress-bar wiring; the pipeline's pure path passes ``None``.

    Returns:
        A list the same length as *candidates*. Each entry is either a
        :class:`Synthesis` or the :class:`BaseException` raised on its behalf.
    """
    if not candidates:
        return []

    async def _run_one(candidate: Candidate) -> Synthesis | BaseException:
        try:
            result: Synthesis | BaseException = await synthesize(
                candidate, ctx, llm, config, model=model, max_tokens=max_tokens
            )
        except Exception as exc:  # noqa: BLE001 — record failure as a list entry
            result = exc
        if on_candidate_done is not None:
            on_candidate_done(result if isinstance(result, BaseException) else None)
        return result

    first = await _run_one(candidates[0])
    rest_results = await gather_resilient(*(_run_one(c) for c in candidates[1:]))
    return [first, *rest_results]
