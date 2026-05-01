import re
from dataclasses import dataclass, field
from typing import Any

import pytest

from slopmortem.llm.cassettes import llm_cassette_key
from slopmortem.llm.prompts import prompt_template_sha
from slopmortem.models import Candidate, Facets


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


@dataclass
class FakeCorpus:
    """In-memory read-side :class:`Corpus` for pipeline tests; no Qdrant, no fastembed."""

    candidates: list[Candidate]
    queries: list[dict[str, object]] = field(default_factory=list)

    async def query(  # noqa: PLR0913 - Protocol contract dictates the signature
        self,
        *,
        dense: list[float],
        sparse: dict[int, float],
        facets: Facets,
        cutoff_iso: str | None,
        strict_deaths: bool,
        k_retrieve: int,
    ) -> list[Candidate]:
        self.queries.append(
            {
                "dense_dim": len(dense),
                "sparse_keys": list(sparse.keys()),
                "facets": facets.model_dump(),
                "cutoff_iso": cutoff_iso,
                "strict_deaths": strict_deaths,
                "k_retrieve": k_retrieve,
            }
        )
        return list(self.candidates[:k_retrieve])

    async def get_post_mortem(self, canonical_id: str) -> str:
        for c in self.candidates:
            if c.canonical_id == canonical_id:
                return c.payload.body
        msg = f"unknown canonical_id {canonical_id!r}"
        raise KeyError(msg)

    async def search_corpus(
        self, q: str, facets: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:
        del q, facets
        return [
            {
                "canonical_id": c.canonical_id,
                "name": c.payload.name,
                "summary": c.payload.summary,
                "score": c.score,
            }
            for c in self.candidates
        ]


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


# Cassettes use pytest-recording (vcrpy). Don't add respx alongside it. Both
# patch the same httpx transport, whichever loads last wins, and you end up
# with flakes in unrelated tests depending on fixture order.
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
