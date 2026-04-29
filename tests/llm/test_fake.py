import pytest

from slopmortem.llm.cassettes import llm_cassette_key
from slopmortem.llm.client import CompletionResult, LLMClient
from slopmortem.llm.fake import FakeLLMClient, FakeResponse, NoCannedResponseError


def _key(*, prompt: str, template_sha: str, model: str) -> tuple[str, str, str]:
    return llm_cassette_key(prompt=prompt, system=None, template_sha=template_sha, model=model)


async def test_returns_canned_response_for_matching_template_and_model():
    canned = {
        _key(
            prompt="anything", template_sha="abc123", model="anthropic/claude-haiku-4.5"
        ): FakeResponse(text="hello world"),
    }
    fake = FakeLLMClient(canned=canned, default_model="anthropic/claude-haiku-4.5")
    r = await fake.complete(
        "anything", model="anthropic/claude-haiku-4.5", extra_body={"prompt_template_sha": "abc123"}
    )
    assert r.text == "hello world"
    assert r.stop_reason == "stop"


async def test_falls_back_to_default_model():
    canned = {
        _key(
            prompt="anything", template_sha="abc123", model="anthropic/claude-haiku-4.5"
        ): FakeResponse(text="ok"),
    }
    fake = FakeLLMClient(canned=canned, default_model="anthropic/claude-haiku-4.5")
    r = await fake.complete("anything", extra_body={"prompt_template_sha": "abc123"})
    assert r.text == "ok"


async def test_missing_canned_response_raises_explicit():
    fake = FakeLLMClient(canned={}, default_model="anthropic/claude-haiku-4.5")
    with pytest.raises(NoCannedResponseError) as ei:
        await fake.complete("anything", extra_body={"prompt_template_sha": "missing"})
    msg = str(ei.value)
    assert "missing" in msg
    assert "anthropic/claude-haiku-4.5" in msg


async def test_records_calls_for_assertion():
    canned = {
        _key(
            prompt="first", template_sha="abc123", model="anthropic/claude-haiku-4.5"
        ): FakeResponse(text="x"),
        _key(
            prompt="second", template_sha="abc123", model="anthropic/claude-haiku-4.5"
        ): FakeResponse(text="x"),
    }
    fake = FakeLLMClient(canned=canned, default_model="anthropic/claude-haiku-4.5")
    await fake.complete("first", extra_body={"prompt_template_sha": "abc123"})
    await fake.complete("second", extra_body={"prompt_template_sha": "abc123"})
    assert len(fake.calls) == 2
    assert fake.calls[0].prompt == "first"
    assert fake.calls[1].prompt == "second"


async def test_propagates_cost_and_cache_metrics():
    canned = {
        _key(prompt="x", template_sha="abc", model="m"): FakeResponse(
            text="t", cost_usd=0.5, cache_read_tokens=10, cache_creation_tokens=20
        ),
    }
    fake = FakeLLMClient(canned=canned, default_model="m")
    r = await fake.complete("x", extra_body={"prompt_template_sha": "abc"})
    assert r.cost_usd == 0.5
    assert r.cache_read_tokens == 10
    assert r.cache_creation_tokens == 20


def test_satisfies_llmclient_protocol():
    fake = FakeLLMClient(canned={}, default_model="m")
    # Protocol is runtime_checkable.
    assert isinstance(fake, LLMClient)


async def test_canned_can_be_completionresult_directly():
    canned = {
        _key(prompt="x", template_sha="abc", model="m"): CompletionResult(
            text="precooked", stop_reason="stop", cost_usd=0.1
        ),
    }
    fake = FakeLLMClient(canned=canned, default_model="m")
    r = await fake.complete("x", extra_body={"prompt_template_sha": "abc"})
    assert r.text == "precooked"
    assert r.cost_usd == 0.1
