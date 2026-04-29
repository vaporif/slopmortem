"""Regression tests for ``safe_post``: scheme + DNS-pinned SSRF guard, body passthrough."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from slopmortem.http import SSRFBlockedError, safe_post

if TYPE_CHECKING:
    from collections.abc import Callable


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:6333/",
        "http://10.0.0.1/admin",
        "http://metadata.google.internal/",
        "file:///etc/passwd",
    ],
)
async def test_safe_post_blocks(url: str) -> None:
    """``safe_post`` refuses the same hosts/schemes as ``safe_get``."""
    with pytest.raises(SSRFBlockedError):
        await safe_post(url, json={})


async def test_safe_post_rejects_non_http_scheme() -> None:
    """Non-http schemes raise SSRFBlockedError mentioning the scheme."""
    with pytest.raises(SSRFBlockedError, match="non-http"):
        await safe_post("file:///etc/passwd", json={})


async def test_safe_post_rejects_loopback_via_dns_rebind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hostname that resolves to 127.0.0.1 is rejected even with an https URL."""

    def _stub(_host: str) -> list[str]:
        return ["127.0.0.1"]

    monkeypatch.setattr("slopmortem.http._resolve_all", _stub)
    with pytest.raises(SSRFBlockedError, match="blocked address"):
        await safe_post("https://evil.example.com/path", json={"x": 1})


async def test_safe_post_passes_json_body_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """The JSON body is forwarded verbatim to ``httpx.AsyncClient.post``."""
    fake_response = httpx.Response(200, json={"ok": True})
    captured: dict[str, object] = {}

    async def fake_post(
        self: object,
        url: str,
        **kwargs: object,
    ) -> httpx.Response:
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        captured["headers"] = kwargs.get("headers")
        return fake_response

    def _stub(_host: str) -> list[str]:
        return ["1.2.3.4"]

    # Bypass DNS resolution so we don't actually hit the network.
    monkeypatch.setattr("slopmortem.http._resolve_all", _stub)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    resp = await safe_post(
        "https://api.tavily.com/search",
        json={"query": "x"},
        headers={"Content-Type": "application/json"},
    )
    assert resp.json() == {"ok": True}
    assert captured["json"] == {"query": "x"}
    assert captured["url"] == "https://api.tavily.com/search"


async def test_safe_post_uses_dns_pinning_helper_shared_with_safe_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refactor invariant: a single ``_resolve_all`` call gates both helpers.

    Both ``safe_get`` and ``safe_post`` must route through the same
    DNS-pinning code path; monkeypatching ``_resolve_all`` to a stub that
    counts calls verifies neither helper went around it.
    """
    calls: list[str] = []

    def counting_resolve(host: str) -> list[str]:
        calls.append(host)
        return ["1.2.3.4"]

    monkeypatch.setattr("slopmortem.http._resolve_all", counting_resolve)

    fake_post: Callable[..., object] = AsyncMock(return_value=httpx.Response(200, json={}))
    fake_get: Callable[..., object] = AsyncMock(return_value=httpx.Response(200, json={}))
    # Patch the AsyncClient methods so no real socket is opened.
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    # Skip the actual transport creation in safe_get's DNS-pinned path
    # by avoiding real I/O — the AsyncClient methods above are stubbed.
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", MagicMock)

    from slopmortem.http import safe_get  # local import keeps top of file lean  # noqa: PLC0415

    await safe_post("https://api.example.com/", json={"k": "v"})
    await safe_get("https://api.example.com/")
    assert calls == ["api.example.com", "api.example.com"]
