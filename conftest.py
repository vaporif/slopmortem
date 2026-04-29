from __future__ import annotations

import re

import pytest

from slopmortem.llm.cassettes import llm_cassette_key
from slopmortem.llm.prompts import prompt_template_sha


def llm_canned_key(
    template_name: str,
    *,
    model: str,
    prompt: str,
    system: str | None = None,
) -> tuple[str, str, str]:
    """Build the 3-tuple key the same way `FakeLLMClient` does internally."""
    tsha = prompt_template_sha(template_name)
    return llm_cassette_key(prompt=prompt, system=system, template_sha=tsha, model=model)


SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(?i)sk-(?:ant-(?:admin\d+-|api\d+-)?|proj-|svcacct-|or-v1-)?[A-Za-z0-9_\-]{20,}"
        ),
        "SCRUBBED",
    ),
    (re.compile(r"tvly-[A-Za-z0-9]{20,}"), "SCRUBBED"),
    (re.compile(r"lmnr_[A-Za-z0-9]{20,}"), "SCRUBBED"),
    (
        re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
        "SCRUBBED",
    ),
    (re.compile(r"AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}"), "SCRUBBED"),
    (re.compile(r"ya29\.[A-Za-z0-9_\-]+"), "SCRUBBED"),
    (re.compile(r"ghp_[A-Za-z0-9]{36}"), "SCRUBBED"),
]
HEADER_ALLOWLIST = {
    "Authorization",
    "x-api-key",
    "x-anthropic-api-key",
    "openai-api-key",
    "openrouter-api-key",
}


def _scrub_body(body: bytes | str) -> bytes:
    """Run body bytes/str through every SECRET_PATTERNS regex.

    Public so tests can assert the regex set catches a representative secret
    (see ``tests/llm/test_secrets_scrub.py``).
    """
    s = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    for pat, repl in SECRET_PATTERNS:
        s = pat.sub(repl, s)
    return s.encode()


@pytest.fixture(scope="module")
def vcr_config():
    def before_record_request(req):
        req.headers = {
            k: ("SCRUBBED" if k in HEADER_ALLOWLIST else v) for k, v in req.headers.items()
        }
        if req.body:
            req.body = _scrub_body(req.body)
        return req

    def before_record_response(resp):
        body = resp.get("body") or {}
        if body.get("string"):
            body["string"] = _scrub_body(body["string"])
            resp["body"] = body
        return resp

    return {
        "filter_headers": list(HEADER_ALLOWLIST),
        "before_record_request": before_record_request,
        "before_record_response": before_record_response,
        "record_mode": "none",
        "match_on": ("method", "scheme", "host", "port", "path", "query", "body"),
    }
