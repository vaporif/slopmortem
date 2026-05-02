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
    """Produce a one-paragraph summary used as ``payload.summary`` by ``llm_rerank``.

    Args:
        text: Canonical body to summarize.
        llm: An :class:`~slopmortem.llm.client.LLMClient` (real or fake).
        model: Optional model id override. Defaults to the client's setting.
        source_id: Source attribution embedded in the prompt's
            ``<untrusted_document>`` tag. Defaults to empty — the summary text
            isn't keyed on it.
        max_tokens: Optional cap on completion tokens. ``None`` keeps the
            client default (no cap sent upstream).

    Returns:
        Stripped LLM output. The ≤400-token cap is a prompt-level contract,
        not enforced inside this function.
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
