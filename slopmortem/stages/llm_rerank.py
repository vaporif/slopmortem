"""LLM rerank stage: one strict-mode JSON call returning :class:`LlmRerankResult`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from slopmortem.errors import RerankLengthError
from slopmortem.llm.prompts import prompt_template_sha, render_prompt
from slopmortem.llm.tools import to_strict_response_schema
from slopmortem.models import LlmRerankResult

if TYPE_CHECKING:
    from slopmortem.config import Config
    from slopmortem.llm.client import LLMClient
    from slopmortem.models import Candidate, Facets


async def llm_rerank(
    candidates: list[Candidate],
    pitch: str,
    facets: Facets,
    llm: LLMClient,
    config: Config,
    *,
    model: str | None = None,
) -> LlmRerankResult:
    """Rerank ``candidates`` against ``pitch`` via one structured-output LLM call.

    Spec lines 220-227, 702-715: Sonnet (or whichever model the caller picks)
    receives every candidate's ``summary`` (NOT ``body``) plus the user pitch
    and the extracted facets, then returns a :class:`LlmRerankResult` whose
    ``ranked`` array length must equal :attr:`Config.N_synthesize`. Strict-mode
    JSON schema constrains shape but not length, so this stage re-validates
    post-parse and raises :class:`RerankLengthError` on mismatch.

    Args:
        candidates: Up to ``Config.K_retrieve`` candidates from the retrieve
            stage.
        pitch: User's input description, passed verbatim into the prompt.
        facets: Extracted facets, dumped into the prompt as JSON for the
            rerank rubric.
        llm: Async :class:`LLMClient` impl. ``cache=True`` is set so the
            shared rubric block hits the prompt cache across calls within
            the 5-min TTL.
        config: :class:`Config` — ``N_synthesize`` is the load-bearing knob
            for the post-parse length check.
        model: Optional override of the LLM client's default model. ``None``
            lets the client pick.

    Returns:
        Parsed :class:`LlmRerankResult` with ``ranked`` length equal to
        ``Config.N_synthesize``.

    Raises:
        RerankLengthError: When the LLM returns an array of the wrong length.
    """
    prompt = render_prompt(
        "llm_rerank",
        pitch=pitch,
        facets=facets.model_dump(),
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
    )
    parsed = LlmRerankResult.model_validate_json(result.text)
    if len(parsed.ranked) != config.N_synthesize:
        msg = f"expected {config.N_synthesize}, got {len(parsed.ranked)}"
        raise RerankLengthError(msg)
    return parsed
