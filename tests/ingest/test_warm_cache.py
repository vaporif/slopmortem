"""Edge-case branches for cache_warm and cache_read_ratio_event."""

from __future__ import annotations

import typing
from dataclasses import dataclass

from conftest import llm_canned_key
from slopmortem.ingest._warm_cache import cache_read_ratio_event, cache_warm
from slopmortem.llm import FakeLLMClient, FakeResponse, render_prompt
from slopmortem.tracing import SpanEvent

_HAIKU = "anthropic/claude-haiku-4.5"


@dataclass
class _StubFanout:
    cache_read: int
    cache_creation: int


class _RaisingLLM(FakeLLMClient):
    @typing.override
    async def complete(self, prompt, **kw):
        msg = "simulated transport failure inside cache_warm"
        raise RuntimeError(msg)


async def test_cache_warm_returns_failure_tuple_when_llm_raises():
    """The warm call is best-effort; one bad warm must not abort the whole ingest."""
    llm = _RaisingLLM(canned={}, default_model=_HAIKU)
    warmed, creation, events = await cache_warm(
        llm=llm, model=_HAIKU, seed_text="seed body", max_tokens=128
    )
    assert warmed is False
    assert creation == 0
    assert events == [SpanEvent.CACHE_WARM_FAILED.value]


async def test_cache_warm_returns_failure_when_creation_tokens_zero():
    """A response with cache_creation_tokens==0 means the cache wasn't actually written."""
    seed = "seed body"
    prompt = render_prompt("summarize", body=seed, source_id="warm")
    canned = {
        llm_canned_key("summarize", model=_HAIKU, prompt=prompt): FakeResponse(
            text="ok", cache_creation_tokens=0, cache_read_tokens=0
        ),
    }
    llm = FakeLLMClient(canned=canned, default_model=_HAIKU)
    warmed, creation, events = await cache_warm(
        llm=llm, model=_HAIKU, seed_text=seed, max_tokens=128
    )
    assert warmed is False
    assert creation == 0
    assert events == [SpanEvent.CACHE_WARM_FAILED.value]


async def test_cache_warm_succeeds_when_creation_tokens_positive():
    """Sanity check the success path so the failure tests aren't testing a dead branch."""
    seed = "seed body"
    prompt = render_prompt("summarize", body=seed, source_id="warm")
    canned = {
        llm_canned_key("summarize", model=_HAIKU, prompt=prompt): FakeResponse(
            text="ok", cache_creation_tokens=512, cache_read_tokens=0
        ),
    }
    llm = FakeLLMClient(canned=canned, default_model=_HAIKU)
    warmed, creation, events = await cache_warm(
        llm=llm, model=_HAIKU, seed_text=seed, max_tokens=128
    )
    assert warmed is True
    assert creation == 512
    assert events == []


def test_cache_read_ratio_event_returns_none_for_empty_probe():
    assert cache_read_ratio_event([], threshold=0.8, probe_n=5) is None


def test_cache_read_ratio_event_returns_none_when_no_tokens_flow():
    """All-zero counters mean the probe is uninformative; suppress the event."""
    probe = [_StubFanout(cache_read=0, cache_creation=0) for _ in range(5)]
    assert cache_read_ratio_event(probe, threshold=0.8, probe_n=5) is None


def test_cache_read_ratio_event_returns_none_when_above_threshold():
    probe = [_StubFanout(cache_read=900, cache_creation=100) for _ in range(5)]
    assert cache_read_ratio_event(probe, threshold=0.8, probe_n=5) is None


def test_cache_read_ratio_event_emits_warning_below_threshold():
    probe = [_StubFanout(cache_read=10, cache_creation=100) for _ in range(5)]
    assert (
        cache_read_ratio_event(probe, threshold=0.8, probe_n=5)
        == SpanEvent.CACHE_READ_RATIO_LOW.value
    )


def test_cache_read_ratio_event_clips_to_probe_n():
    head = [_StubFanout(cache_read=10, cache_creation=100)] * 5
    tail = [_StubFanout(cache_read=900, cache_creation=10)] * 20
    assert (
        cache_read_ratio_event([*head, *tail], threshold=0.8, probe_n=5)
        == SpanEvent.CACHE_READ_RATIO_LOW.value
    )
