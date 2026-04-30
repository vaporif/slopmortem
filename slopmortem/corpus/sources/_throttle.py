"""Per-host throttle and robots.txt cache for source adapters.

* :func:`throttle_for`: process-wide token bucket keyed on host. Default budget
  is 1 request per second per host. Built on an in-memory dict rather than
  ``aiolimiter`` since the call surface is ~30 LOC and a new dep isn't worth it.
* :func:`respect_robots`: fetch and parse the host's ``robots.txt`` once per
  process, then ask :class:`urllib.robotparser.RobotFileParser` whether *url*
  is permitted for the configured user agent.

Both helpers send outbound HTTP through :func:`slopmortem.http.safe_get` and
don't care which source adapter calls them. Curated, HN, and Wayback all share
the same instance. Crunchbase CSV reads the filesystem and skips both.
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

# Process-wide last-call timestamps and robots.txt cache.
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
    """Sleep just long enough that the next outbound call to *url*'s host respects *rps*.

    The token bucket is process-wide and keyed on hostname. Sequential calls to
    the same host are at least ``1/rps`` seconds apart; calls to different hosts
    are independent.

    Args:
        url: The URL about to be fetched.
        rps: Requests-per-second budget for this host. Defaults to 1.0.
    """
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
    """Clear the per-host throttle and robots cache. Tests use this between cases."""
    _last_call.clear()
    _robots_cache.clear()


async def _load_robots(host: str, scheme: str) -> RobotFileParser | None:
    robots_url = f"{scheme}://{host}/robots.txt"
    try:
        resp = await safe_get(robots_url)
    except (SSRFBlockedError, httpx.HTTPError):
        # No robots fetched. Default to "allowed" by returning None.
        return None
    if resp.status_code >= HTTP_BAD_REQUEST:
        return None
    rp = RobotFileParser()
    rp.parse(resp.text.splitlines())
    return rp


async def respect_robots(url: str, *, user_agent: str = USER_AGENT) -> bool:
    """Return whether *user_agent* is allowed to fetch *url* per robots.txt.

    Returns ``True`` on any failure to fetch or parse robots.txt. Robots is
    etiquette, not a security boundary; the SSRF wrapper is the real
    outbound-network gate.

    Args:
        url: The URL the caller wants to fetch.
        user_agent: UA token to check rules against.

    Returns:
        ``True`` if the URL is permitted (or robots is unreachable), else ``False``.
    """
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
