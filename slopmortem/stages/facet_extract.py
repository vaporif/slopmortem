"""Facet-extract stage: one strict-mode JSON LLM call → :class:`Facets`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lmnr import observe

from slopmortem.llm import prompt_template_sha, render_prompt, to_strict_response_schema
from slopmortem.models import Facets

if TYPE_CHECKING:
    from slopmortem.llm import LLMClient


@observe(name="stage.facet_extract")
async def extract_facets(
    text: str,
    llm: LLMClient,
    model: str | None = None,
    *,
    max_tokens: int | None = None,
) -> Facets:
    """Malformed or out-of-taxonomy output raises Pydantic ``ValidationError``;
    per-entry ingest isolation absorbs this without aborting the run.
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
