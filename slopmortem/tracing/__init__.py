"""Init guard for the laminar tracer. Refuses non-loopback endpoints by default.

Also exposes helpers for run-identity attributes on root spans:
:func:`mint_run_id` and :func:`git_sha`.
"""

from __future__ import annotations

import functools
import ipaddress
import socket
import subprocess
from urllib.parse import urlparse

from uuid_extensions import uuid7str

from slopmortem.tracing.events import SpanEvent as SpanEvent


class TracingGuardError(RuntimeError):
    pass


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
    # Refuse non-loopback endpoints by default.
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


def mint_run_id() -> str:
    return uuid7str().replace("-", "")


@functools.cache
def git_sha() -> str | None:
    # Memoized per process; returns None outside a git checkout.
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 — git on PATH is fine.
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return None
    return out.stdout.strip() or None
