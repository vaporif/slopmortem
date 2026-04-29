"""Per-host throttle and robots.txt enforcement tests.

* Throttle: two consecutive calls to the same host are >=1s apart at the default
  1 rps budget. Calls to different hosts are independent.
* Robots: a ``User-agent: *\nDisallow: /private/`` rule on a host blocks
  ``/private/foo`` and permits ``/public/foo``.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock

import pytest

from slopmortem.corpus.sources import _throttle
from slopmortem.corpus.sources._throttle import (
    DEFAULT_RPS,
    USER_AGENT,
    reset_throttle_state,
    respect_robots,
    throttle_for,
)


@pytest.fixture(autouse=True)
def _reset_state() -> None:  # pyright: ignore[reportUnusedFunction]
    reset_throttle_state()


class _FakeResp:
    def __init__(self, body: str, status: int = 200) -> None:
        self.text = body
        self.status_code = status


async def test_per_host_throttle_caps_one_rps() -> None:
    """Two consecutive throttle_for() to the same host are >=1s apart at 1 rps."""
    url = "https://example.com/page"
    t0 = time.monotonic()
    await throttle_for(url, rps=DEFAULT_RPS)
    await throttle_for(url, rps=DEFAULT_RPS)
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.95, f"expected >=1s gap, got {elapsed:.3f}s"


async def test_throttle_independent_per_host() -> None:
    """Different hosts do not share the bucket."""
    t0 = time.monotonic()
    await throttle_for("https://a.example/x", rps=DEFAULT_RPS)
    await throttle_for("https://b.example/x", rps=DEFAULT_RPS)
    elapsed = time.monotonic() - t0
    # Two calls to two distinct hosts complete in well under 1 second.
    assert elapsed < 0.5


async def test_robots_disallow_blocks_url(monkeypatch: pytest.MonkeyPatch) -> None:
    robots_body = "User-agent: *\nDisallow: /private/\n"

    async def fake_get(url: str, **_kw: object) -> Any:
        assert url == "https://example.com/robots.txt"
        return _FakeResp(robots_body)

    monkeypatch.setattr(_throttle, "safe_get", fake_get)
    assert await respect_robots("https://example.com/private/foo") is False
    assert await respect_robots("https://example.com/public/foo") is True


async def test_robots_unreachable_defaults_to_allow(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 / network error on robots.txt should not block fetches."""
    monkeypatch.setattr(
        _throttle,
        "safe_get",
        AsyncMock(return_value=_FakeResp("", status=404)),
    )
    assert await respect_robots("https://example.com/anything") is True


async def test_robots_user_agent_specific(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per-UA disallow rules narrow the gate to the named UA."""
    robots_body = "User-agent: badbot\nDisallow: /\n\nUser-agent: *\nDisallow:\n"

    async def fake_get(_url: str, **_kw: object) -> Any:
        return _FakeResp(robots_body)

    monkeypatch.setattr(_throttle, "safe_get", fake_get)
    assert await respect_robots("https://example.com/x", user_agent="badbot") is False
    assert await respect_robots("https://example.com/x", user_agent=USER_AGENT) is True
