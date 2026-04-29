from __future__ import annotations

import tiktoken

from slopmortem.corpus.summarize import summarize_for_rerank
from slopmortem.llm.fake import FakeLLMClient, FakeResponse
from slopmortem.llm.prompts import prompt_template_sha

_HAIKU = "anthropic/claude-haiku-4.5"
_SUMMARY_TEXT = (
    "Acme was a B2B widget marketplace. It sold to mid-market manufacturers, raised a small "
    "seed in 2018, never found product-market fit, and shut down in 2021 after running out of "
    "cash. The team cited weak demand signal and an over-built MVP."
)


def _fake_llm_for_summarize(text: str) -> FakeLLMClient:
    return FakeLLMClient(
        canned={(prompt_template_sha("summarize"), _HAIKU): FakeResponse(text=text)},
        default_model=_HAIKU,
    )


async def test_summarize_under_400_tokens():
    long_text = "Acme failed because... " * 500
    llm = _fake_llm_for_summarize(_SUMMARY_TEXT)
    summary = await summarize_for_rerank(long_text, llm)
    assert isinstance(summary, str)
    assert summary.strip()
    enc = tiktoken.get_encoding("cl100k_base")
    assert len(enc.encode(summary)) <= 400


async def test_summarize_uses_llm_via_protocol():
    llm = _fake_llm_for_summarize(_SUMMARY_TEXT)
    summary = await summarize_for_rerank("startup body text", llm)
    assert summary
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call.template_sha == prompt_template_sha("summarize")
    assert call.model == _HAIKU
    assert call.cache is True


async def test_summarize_strips_whitespace():
    llm = _fake_llm_for_summarize("   final summary text\n\n")
    summary = await summarize_for_rerank("body", llm)
    assert summary == "final summary text"
