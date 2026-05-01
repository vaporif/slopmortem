"""Facet-extract stage: one LLM call returning a strict-mode :class:`Facets` JSON object."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lmnr import observe

from slopmortem.llm.prompts import prompt_template_sha, render_prompt
from slopmortem.llm.tools import to_strict_response_schema
from slopmortem.models import Facets

if TYPE_CHECKING:
    from slopmortem.llm.client import LLMClient


@observe(name="stage.facet_extract")
async def extract_facets(
    text: str,
    llm: LLMClient,
    model: str | None = None,
    *,
    max_tokens: int | None = None,
) -> Facets:
    """Extract a :class:`Facets` bundle from *text* via one strict-mode JSON call.

    Args:
        text: Description text the LLM pulls facets from.
        llm: Async LLM client honoring the :class:`LLMClient` Protocol.
        model: Optional model override. ``None`` lets the client pick.
        max_tokens: Optional output cap forwarded to the LLM client.

    Returns:
        Parsed :class:`Facets`, taxonomy-validated by the model's
        ``model_validator``. Strict raise: any malformed or out-of-taxonomy
        output surfaces as a Pydantic ``ValidationError`` to the caller.
        Per-entry ingest isolation already absorbs this without aborting
        the run.
    """
    prompt = render_prompt("facet_extract", description=text)
    result = await llm.complete(
        prompt,
        model=model,
        cache=True,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "Facets",
                "schema": to_strict_response_schema(Facets),
                "strict": True,
            },
        },
        extra_body={"prompt_template_sha": prompt_template_sha("facet_extract")},
        max_tokens=max_tokens,
    )
    return Facets.model_validate_json(result.text)
