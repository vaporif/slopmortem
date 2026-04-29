"""Init guard for the laminar tracer. Refuses non-loopback endpoints by default."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class TracingGuardError(RuntimeError):
    """Raised when ``init_tracing`` refuses a tracer endpoint on policy grounds."""


PRIVATE_HOST_ALLOWLIST: set[str] = set()


def _resolve_all(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return []
    return list({str(info[4][0]) for info in infos})


def _all_loopback(addrs: list[str]) -> bool:
    if not addrs:
        return False
    return all(ipaddress.ip_address(a).is_loopback for a in addrs)


def init_tracing(base_url: str | None = None, *, allow_remote: bool = False) -> None:
    """Validate *base_url* before letting the tracer phone home."""
    if not base_url:
        return
    host = urlparse(base_url).hostname
    if not host:
        msg = f"missing host in {base_url!r}"
        raise TracingGuardError(msg)
    addrs = _resolve_all(host)
    is_safe = _all_loopback(addrs) or host in PRIVATE_HOST_ALLOWLIST
    if not is_safe and not allow_remote:
        msg = (
            f"refusing tracing to non-loopback {host} (resolved: {addrs}); "
            "set LMNR_ALLOW_REMOTE=1 to override"
        )
        raise TracingGuardError(msg)
