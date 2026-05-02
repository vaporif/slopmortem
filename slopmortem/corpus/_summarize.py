"""LLM-backed summarizer that produces ``payload.summary`` for the rerank stage.

Runs at ingest time, between :mod:`facet_extract` and :mod:`embed_dense`.
The 400-token cap is a contract on the LLM's output, enforced by the
``Stay under 120 words`` directive in
``slopmortem/llm/prompts/summarize.j2``. The rerank stage budgets
``K * summary`` tokens for its input window and checks accordingly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from slopmortem.llm import prompt_template_sha, render_prompt

if TYPE_CHECKING:
    from slopmortem.llm import LLMClient


async def summarize_for_rerank(
    text: str,
    llm: LLMClient,
    *,
    model: str | None = None,
    source_id: str = "",
    max_tokens: int | None = None,
) -> str:
    """Produce ``payload.summary`` for the rerank stage.

    The ≤400-token cap is a prompt-level contract (see ``summarize.j2``),
    not enforced here. ``source_id`` is embedded in the prompt's
    ``<untrusted_document>`` tag.
    """
    prompt = render_prompt("summarize", body=text, source_id=source_id)
    result = await llm.complete(
        prompt,
        model=model,
        cache=True,
        extra_body={"prompt_template_sha": prompt_template_sha("summarize")},
        max_tokens=max_tokens,
    )
    return result.text.strip()
