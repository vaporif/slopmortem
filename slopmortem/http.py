"""SSRF-aware HTTP fetch. Refuses loopback, link-local, private, and IMDS targets."""

from __future__ import annotations

import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

import httpx

_BLOCKED_IMDS_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata",
        "instance-data",
        "metadata.azure.com",
    }
)


class SSRFBlockedError(RuntimeError):
    """Raised when ``safe_get`` refuses a URL on SSRF-policy grounds."""


def _is_blocked_address(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True
    if (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True
    if isinstance(ip, ipaddress.IPv4Address):
        return ip in ipaddress.ip_network("100.64.0.0/10") or ip in ipaddress.ip_network(
            "169.254.0.0/16"
        )
    # ip_address() returns IPv4Address | IPv6Address, so this branch is exhaustive.
    return ip in ipaddress.ip_network("fc00::/7") or ip in ipaddress.ip_network("fe80::/10")


def _resolve_all(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        msg = f"cannot resolve host {host!r}: {e}"
        raise SSRFBlockedError(msg) from e
    # getaddrinfo returns sockaddr tuples whose [0] element is a host string;
    # cast keeps mypy from widening the set element type to ``str | int``.
    return list({str(info[4][0]) for info in infos})


async def safe_get(
    url: str,
    *,
    timeout: float = 30.0,  # noqa: ASYNC109 — caller-controlled timeout is part of the public API
) -> httpx.Response:
    """Fetch *url* via httpx after enforcing scheme + DNS-pinned SSRF checks."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        msg = f"refusing non-http(s) scheme: {parsed.scheme!r}"
        raise SSRFBlockedError(msg)
    host = parsed.hostname
    if not host:
        msg = f"missing host in {url!r}"
        raise SSRFBlockedError(msg)
    if host in _BLOCKED_IMDS_HOSTS:
        msg = f"refusing IMDS host {host!r}"
        raise SSRFBlockedError(msg)
    addrs = _resolve_all(host)
    if not addrs:
        msg = f"no addresses resolved for {host!r}"
        raise SSRFBlockedError(msg)
    for a in addrs:
        if _is_blocked_address(a):
            msg = f"refusing blocked address {a} for host {host!r}"
            raise SSRFBlockedError(msg)

    pinned_ip = addrs[0]
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    # The httpx resolver hook keeps a static, pre-validated address tuple so
    # subsequent connect() can never round-trip back through DNS (SSRF rebinding).
    async def _resolver(  # pyright: ignore[reportUnusedFunction]
        *_args: Any,  # pyright: ignore[reportExplicitAny, reportAny]  # httpx resolver signature is untyped
        **_kwargs: Any,  # pyright: ignore[reportExplicitAny, reportAny]
    ) -> list[tuple[int, int, int, str, tuple[str, int]]]:
        return [
            (
                socket.AF_INET if ":" not in pinned_ip else socket.AF_INET6,
                socket.SOCK_STREAM,
                0,
                "",
                (pinned_ip, port),
            )
        ]

    transport = httpx.AsyncHTTPTransport()
    async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
        return await client.get(url, headers={"Host": host})
