"""LLM rerank stage: one strict-mode JSON call returning :class:`LlmRerankResult`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lmnr import Laminar, observe

from slopmortem.errors import RerankLengthError
from slopmortem.llm import prompt_template_sha, render_prompt, to_strict_response_schema
from slopmortem.models import LlmRerankResult

if TYPE_CHECKING:
    from slopmortem.config import Config
    from slopmortem.llm import LLMClient
    from slopmortem.models import Candidate, Facets


# ``ignore_inputs=["candidates"]`` matches the top-level parameter name only
# (lmnr-python's filter is ``k in ignore_inputs`` against
# ``inspect.signature(func).parameters.keys()``). Dropping the Candidate list
# keeps payload.body out of span attributes; a redacted ``(canonical_id, name)``
# projection is re-attached via Laminar.set_span_attributes. Output
# (LlmRerankResult) carries no body and stays auto-captured.
@observe(name="stage.llm_rerank", ignore_inputs=["candidates"])
async def llm_rerank(  # noqa: PLR0913 — every dependency is required at the call site
    candidates: list[Candidate],
    pitch: str,
    facets: Facets,
    llm: LLMClient,
    config: Config,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
) -> LlmRerankResult:
    """Rerank ``candidates`` against ``pitch`` via one structured-output LLM call.

    Sonnet (or whichever model the caller picks) gets every candidate's
    ``summary`` (NOT ``body``) plus the user pitch and extracted facets, then
    returns a :class:`LlmRerankResult` whose ``ranked`` length is
    ``min(Config.N_synthesize, len(candidates))``. Strict-mode JSON schema
    constrains shape but not length, so this stage re-validates post-parse
    and raises :class:`RerankLengthError` on mismatch.

    Args:
        candidates: Up to ``Config.K_retrieve`` candidates from retrieve.
        pitch: User's input description, passed verbatim into the prompt.
        facets: Extracted facets, dumped into the prompt as JSON for the
            rerank rubric.
        llm: Async :class:`LLMClient`. ``cache=True`` so the shared rubric
            block hits the prompt cache across calls within the 5-min TTL.
        config: :class:`Config`. ``N_synthesize`` is the load-bearing knob
            for the post-parse length check.
        model: Optional override of the LLM client's default model. ``None``
            lets the client pick.
        max_tokens: Optional cap on completion tokens. ``None`` keeps the
            client default (no cap sent upstream).

    Returns:
        Parsed :class:`LlmRerankResult` with ``ranked`` length equal to
        ``min(Config.N_synthesize, len(candidates))``.

    Raises:
        RerankLengthError: LLM returned an array of the wrong length.
    """
    Laminar.set_span_attributes(
        {
            "candidates_meta": [
                {"canonical_id": c.canonical_id, "name": c.payload.name} for c in candidates
            ],
        }
    )
    # TODO(scaling): rerank cost grows linearly with K_retrieve (#27).
    # At K=30 the rubric prompt cache absorbs most of the latency. Bump K and
    # the candidate-list segment dominates. Pick later between (a) two-stage
    # rerank (cheap 30→10, expensive 10→5), (b) local cross-encoder (e.g.
    # bge-reranker) replacing the LLM call, or (c) tighter summary truncation.
    # Decide when measurements justify it.
    prompt = render_prompt(
        "llm_rerank",
        pitch=pitch,
        facets=facets.model_dump(),
        top_n=config.N_synthesize,
        candidates=[
            {
                "candidate_id": c.canonical_id,
                "name": c.payload.name,
                "summary": c.payload.summary,
            }
            for c in candidates
        ],
    )
    result = await llm.complete(
        prompt,
        model=model,
        cache=True,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "LlmRerankResult",
                "schema": to_strict_response_schema(LlmRerankResult),
                "strict": True,
            },
        },
        extra_body={"prompt_template_sha": prompt_template_sha("llm_rerank")},
        max_tokens=max_tokens,
    )
    parsed = LlmRerankResult.model_validate_json(result.text)
    expected = min(config.N_synthesize, len(candidates))
    if len(parsed.ranked) != expected:
        msg = f"expected {expected}, got {len(parsed.ranked)}"
        raise RerankLengthError(msg)
    return parsed
