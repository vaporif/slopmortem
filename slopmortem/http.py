"""SSRF-aware HTTP fetch. Refuses loopback, link-local, private, and IMDS targets."""

import ipaddress
import socket
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
    """Raised when ``safe_get`` / ``safe_post`` refuse a URL on SSRF-policy grounds."""


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


def _resolve_and_validate(url: str) -> str:
    """Validate *url*'s scheme, host, and resolved addresses against the SSRF policy.

    Returns the original *url*'s host (for the ``Host`` header). Raises
    :class:`SSRFBlockedError` on any policy failure: non-http(s) scheme,
    missing host, IMDS hostname, unresolvable host, or any resolved
    address falling inside the blocklist (loopback / link-local / private /
    multicast / reserved / unspecified / CGNAT / ULA / IPv6 link-local).

    Shared by :func:`safe_get` and :func:`safe_post` so a single code path
    enforces the policy.
    """
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
    return host


async def safe_get(
    url: str,
    *,
    timeout: float = 30.0,  # noqa: ASYNC109 — caller-controlled timeout is part of the public API
) -> httpx.Response:
    """Fetch *url* via httpx after enforcing scheme and DNS-pinned SSRF checks."""
    host = _resolve_and_validate(url)
    transport = httpx.AsyncHTTPTransport()
    async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
        return await client.get(url, headers={"Host": host})


async def safe_post(
    url: str,
    *,
    json: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,  # noqa: ASYNC109 — caller-controlled timeout is part of the public API
) -> httpx.Response:
    """POST *json* to *url* via httpx after enforcing the same SSRF policy as ``safe_get``.

    Mirrors :func:`safe_get`'s scheme + DNS validation by routing through
    the shared :func:`_resolve_and_validate` helper. Used by the Tavily
    synthesis tools (``/search`` and ``/extract`` are POST-only).
    """
    host = _resolve_and_validate(url)
    merged_headers: dict[str, str] = {"Host": host}
    if headers:
        merged_headers.update(headers)
    transport = httpx.AsyncHTTPTransport()
    async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
        return await client.post(url, json=json, headers=merged_headers)
