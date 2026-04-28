# start_slop — design review: open issues

**Date:** 2026-04-28
**Status:** open — to triage and fold into spec
**Companion to:** [2026-04-27-start-slop-design.md](2026-04-27-start-slop-design.md)

Findings from a five-pass technical review of the design spec, re-verified by
parallel cross-checks against the current spec, current SDK code, and current
vendor docs. Resolved issues are no longer tracked here. Original numbering
retained so external references stay stable.

| # | Issue | Severity | Status |
|---|---|---|---|
| 6 | DNS-rebinding guard cannot bind to SDK pool | should-fix | spec-fixed; impl deferred to v2 |

**Spec line citations in this document are off by ~30–80 lines** (review was
written against an earlier snapshot). Real locations called out in each
section.

---

## #6 — DNS-rebinding guard cannot bind to SDK pool

**Severity:** should-fix — the spec sentence is unimplementable as written,
but on the loopback-default deployment (the spec's normal case, see
spec:185–186, 254, 331) the rebinding window is mostly cosmetic. Original
review framed this as a blocker; the architectural concern is real, the
runtime exposure is small.

**v1 decision (2026-04-28):** spec edited to drop the false TOCTOU claim
and document the residual window on the `LMNR_ALLOW_REMOTE=1` path.
Implementation of Path A (IP-pinning) deferred to v2 — see TODO comments
at spec:619 and spec:722.

### Problem

spec:597 (review's "spec:558"/"spec:664" — the line numbers are off, the
sentence appears once):

> The DNS lookup is repeated per outbound request (TOCTOU mitigation)
> since the initial resolve can change.

What actually happens:

```
user code                    Laminar SDK                  network
─────────                    ───────────                  ───────
Laminar.init(url)  ──►  ┌──────────────────┐
                        │ httpx.Client(...)│
                        │ OTel exporter    │
                        │   keeps own conn │  ──► resolves once
                        │   pool, own DNS  │  ──► caches IP
                        └──────────────────┘  ──► reuses keep-alive
                                │
                                ▼
                        you don't get a hook here
                        ────────────────────────
```

The Laminar SDK manages its own httpx client and OTel exporter. Calling
`socket.gethostbyname()` once at `tracing.init()` does not bind the
result to the SDK's connection pool, and the SDK's later requests will
re-resolve (or use cached connections) without consulting our guard.

### Recommendation

**Path A: fail closed by hard-pinning the resolved IP into the URL.**

```python
def init_tracing(base_url: str, allow_remote: bool = False) -> None:
    parsed = urlparse(base_url)
    host = parsed.hostname
    resolved = socket.gethostbyname(host)
    ip = ipaddress.ip_address(resolved)

    if not (ip.is_loopback or host in PRIVATE_HOST_ALLOWLIST):
        if not allow_remote:
            raise SecurityError(f"refusing tracing to non-loopback {host}")

    # rewrite URL to use the resolved IP, bypassing further DNS
    pinned = parsed._replace(netloc=f"{resolved}:{parsed.port or 443}")
    Laminar.init(base_url=urlunparse(pinned), ...)
```

After this, the SDK never resolves again — there is no second resolution
to TOCTOU. Mention in span attributes that the IP is pinned.

**Caveat for the `LMNR_ALLOW_REMOTE=1` path:** an IP-form URL fails standard
TLS hostname verification because the cert SAN is issued for the hostname,
not the IP. For loopback (the default), the spec uses plain HTTP, so this
doesn't bite. For remote, pair the IP-pinned URL with an explicit
`server_hostname=` SNI override on the underlying transport, or document
that remote deployments accept the (small) rebinding window.

**An earlier Path B (inject a custom httpx transport via `http_client=`) was
considered and dropped:** the Laminar Python SDK's `Laminar.initialize()`
signature does not accept an `http_client` / `transport` parameter (verified
against `lmnr-ai/lmnr-python` `src/lmnr/sdk/laminar.py`). Implementing it
would require either upstreaming the parameter or replacing the OTLP
exporter through OTel internals. Path A is the pragmatic choice.

### Spec edits required

- spec:597 — replace "DNS lookup repeated per outbound request" with "host
  resolved once at init; resolved IP is pinned into `LMNR_BASE_URL` so
  subsequent requests bypass DNS entirely. For `LMNR_ALLOW_REMOTE=1`,
  document the SNI implication."
- Task #1 (Gate 1) — `tracing.py` deliverable: IP-pinning at init,
  explicit test that `Laminar.init` receives an IP-form URL

---

## Recommended fix order

```
should-fix — fix during implementation, in-task:
  #6 DNS guard (spec edited; v2 hardening — see TODOs at spec:619, 722)
```
