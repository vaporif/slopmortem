"""Per-host throttle and robots.txt cache for source adapters.

Process-wide token bucket keyed on host (default 1 req/sec/host) plus a
per-host ``robots.txt`` cache. Outbound HTTP goes through ``safe_get``; one
shared instance for curated, HN, and Wayback. Crunchbase CSV is filesystem-
only and skips both.
"""

from __future__ import annotations

import time
from typing import Final
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import anyio
import httpx

from slopmortem.http import USER_AGENT, SSRFBlockedError, safe_get

DEFAULT_RPS: Final = 1.0
HTTP_BAD_REQUEST: Final = 400

__all__ = [
    "DEFAULT_RPS",
    "HTTP_BAD_REQUEST",
    "USER_AGENT",
    "reset_throttle_state",
    "respect_robots",
    "throttle_for",
]

_last_call: dict[str, float] = {}
_robots_cache: dict[str, RobotFileParser | None] = {}
_robots_lock = anyio.Lock()
_throttle_lock = anyio.Lock()


def _host_of(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.hostname:
        msg = f"missing host in {url!r}"
        raise ValueError(msg)
    return parsed.hostname


async def throttle_for(url: str, *, rps: float = DEFAULT_RPS) -> None:
    """Sequential calls to the same host stay at least ``1/rps`` apart; hosts run independently."""
    host = _host_of(url)
    interval = 1.0 / max(rps, 1e-6)
    async with _throttle_lock:
        now = time.monotonic()
        last = _last_call.get(host, 0.0)
        wait = (last + interval) - now
        if wait > 0:
            await anyio.sleep(wait)
        _last_call[host] = time.monotonic()


def reset_throttle_state() -> None:
    """Tests use this between cases to clear the per-host throttle and robots cache."""
    _last_call.clear()
    _robots_cache.clear()


async def _load_robots(host: str, scheme: str) -> RobotFileParser | None:
    robots_url = f"{scheme}://{host}/robots.txt"
    try:
        resp = await safe_get(robots_url)
    except (SSRFBlockedError, httpx.HTTPError):
        # No robots fetched → default to allowed.
        return None
    if resp.status_code >= HTTP_BAD_REQUEST:
        return None
    rp = RobotFileParser()
    rp.parse(resp.text.splitlines())
    return rp


async def respect_robots(url: str, *, user_agent: str = USER_AGENT) -> bool:
    """Returns ``True`` on any failure — robots is etiquette; the SSRF wrapper is the boundary."""
    parsed = urlparse(url)
    host = parsed.hostname
    scheme = parsed.scheme or "https"
    if not host:
        return True
    async with _robots_lock:
        if host not in _robots_cache:
            _robots_cache[host] = await _load_robots(host, scheme)
        rp = _robots_cache[host]
    if rp is None:
        return True
    return rp.can_fetch(user_agent, url)
