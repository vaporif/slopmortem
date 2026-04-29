from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from slopmortem.budget import Budget
from slopmortem.llm.openrouter import OpenRouterClient
from slopmortem.tracing.events import SpanEvent


def _usage(prompt_cached: int = 0, prompt_cache_write: int = 0, cost: float = 0.001):
    return SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=10,
        total_tokens=110,
        prompt_tokens_details=SimpleNamespace(
            cached_tokens=prompt_cached,
            cache_write_tokens=prompt_cache_write,
        ),
        cost=cost,
    )


def _resp(*, content="ok", usage=None):
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = []
    choice = MagicMock()
    choice.finish_reason = "stop"
    choice.message = msg
    choice.error = None
    r = MagicMock()
    r.choices = [choice]
    r.usage = usage
    return r


@pytest.fixture
def fake_sdk():
    sdk = MagicMock()
    sdk.chat = MagicMock()
    sdk.chat.completions = MagicMock()
    sdk.chat.completions.create = AsyncMock()
    return sdk


async def test_cache_true_with_zero_writes_triggers_rewarm_retry(fake_sdk):
    fake_sdk.chat.completions.create.side_effect = [
        _resp(content="warm-up", usage=_usage(prompt_cached=0, prompt_cache_write=0)),
        _resp(content="warmed", usage=_usage(prompt_cached=0, prompt_cache_write=50)),
    ]
    c = OpenRouterClient(sdk=fake_sdk, budget=Budget(2.0))
    r = await c.complete("hi", cache=True)
    assert r.cache_creation_tokens == 50
    # Two SDK calls: original + re-warm retry.
    assert fake_sdk.chat.completions.create.await_count == 2


async def test_cache_warm_failed_emitted_when_retry_still_zero(fake_sdk, monkeypatch):
    fake_sdk.chat.completions.create.side_effect = [
        _resp(usage=_usage(prompt_cache_write=0)),
        _resp(usage=_usage(prompt_cache_write=0)),
    ]
    c = OpenRouterClient(sdk=fake_sdk, budget=Budget(2.0))
    emitted: list[SpanEvent] = []
    monkeypatch.setattr(c, "_emit", emitted.append)
    r = await c.complete("hi", cache=True)
    assert r.cache_creation_tokens == 0
    assert SpanEvent.CACHE_WARM_FAILED in emitted


async def test_no_rewarm_when_cache_false(fake_sdk):
    fake_sdk.chat.completions.create.return_value = _resp(usage=_usage())
    c = OpenRouterClient(sdk=fake_sdk, budget=Budget(2.0))
    await c.complete("hi", cache=False)
    assert fake_sdk.chat.completions.create.await_count == 1


async def test_no_rewarm_when_cache_true_and_first_write_nonzero(fake_sdk):
    fake_sdk.chat.completions.create.return_value = _resp(usage=_usage(prompt_cache_write=100))
    c = OpenRouterClient(sdk=fake_sdk, budget=Budget(2.0))
    r = await c.complete("hi", cache=True)
    assert r.cache_creation_tokens == 100
    assert fake_sdk.chat.completions.create.await_count == 1
