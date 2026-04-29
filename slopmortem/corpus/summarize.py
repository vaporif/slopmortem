"""LLM-backed summarizer that produces ``payload.summary`` for the rerank stage.

The summarize stage runs at ingest time, between :mod:`facet_extract` and
:mod:`embed_dense`. The 400-token cap is a contract on the LLM's output —
controlled by the ``Stay under 120 words`` directive in
``slopmortem/llm/prompts/summarize.j2`` — and verified by the rerank stage
which budgets ``K * summary`` tokens for its input window.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from slopmortem.llm.prompts import prompt_template_sha, render_prompt

if TYPE_CHECKING:
    from slopmortem.llm.client import LLMClient


async def summarize_for_rerank(
    text: str,
    llm: LLMClient,
    *,
    model: str | None = None,
    source_id: str = "",
) -> str:
    """Produce a one-paragraph summary used as ``payload.summary`` by ``llm_rerank``.

    Args:
        text: The canonical body to summarize.
        llm: An :class:`~slopmortem.llm.client.LLMClient` (real or fake).
        model: Optional model id override; defaults to whatever the client has.
        source_id: Source attribution embedded in the prompt's ``<untrusted_document>``
            tag. Defaults to empty since the summary text itself isn't keyed on it.

    Returns:
        The stripped LLM output; the ≤400-token cap is a prompt-level contract,
        not enforced inside this function.
    """
    prompt = render_prompt("summarize", body=text, source_id=source_id)
    result = await llm.complete(
        prompt,
        model=model,
        cache=True,
        extra_body={"prompt_template_sha": prompt_template_sha("summarize")},
    )
    return result.text.strip()
