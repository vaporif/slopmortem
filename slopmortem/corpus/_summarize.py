"""LLM-backed summarizer that produces ``payload.summary`` for the rerank stage.

The 400-token / 120-word output cap is a prompt-level contract in
``slopmortem/llm/prompts/summarize.j2`` — the rerank stage budgets
``K * summary`` tokens against it.
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

    ``source_id`` is embedded in the prompt's ``<untrusted_document>`` tag.
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
