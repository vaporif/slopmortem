from __future__ import annotations

import os

import pytest

from conftest import _scrub_body


def test_scrubs_openrouter_key():
    out = _scrub_body(b"Authorization: Bearer sk-or-v1-abcdef1234567890abcdef1234567890")
    assert b"SCRUBBED" in out
    assert b"sk-or-v1-abcdef" not in out


def test_scrubs_anthropic_key():
    out = _scrub_body(b'{"key": "sk-ant-api01-abcdefghijklmnopqrstuvwxyz0123456789"}')
    assert b"SCRUBBED" in out
    assert b"sk-ant" not in out


def test_scrubs_openai_project_key():
    out = _scrub_body(b"sk-proj-abcdefghijklmnopqrstuvwx")
    assert b"SCRUBBED" in out


def test_scrubs_tavily_key():
    out = _scrub_body(b"tvly-abcdef0123456789ABCDEF")
    assert b"SCRUBBED" in out


def test_scrubs_jwt():
    jwt = (
        b"eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        b"abcdefghijklmnop"
    )
    out = _scrub_body(jwt)
    assert b"SCRUBBED" in out


@pytest.mark.vcr
async def test_cassette_miss_loud(monkeypatch):
    if os.environ.get("RUN_LIVE"):
        pytest.skip("live mode")
    from openai import AsyncOpenAI

    sdk = AsyncOpenAI(
        api_key="sk-or-v1-test", base_url="https://openrouter.ai/api/v1"
    )
    with pytest.raises(Exception) as ei:
        await sdk.chat.completions.create(
            model="anthropic/claude-haiku-4.5",
            messages=[{"role": "user", "content": "missing cassette"}],
        )

    # The openai SDK wraps the underlying VCR/HTTP error; walk __cause__/__context__
    # so the test sees the actual "no cassette / record" hint.
    seen: list[str] = []
    cur: BaseException | None = ei.value
    while cur is not None:
        seen.append(f"{type(cur).__name__}: {cur}".lower())
        nxt = cur.__cause__ or cur.__context__
        if nxt is cur:
            break
        cur = nxt
    joined = " | ".join(seen)
    assert (
        "cassette" in joined
        or "cannotoverwrite" in joined
        or "record_mode" in joined
        or "no match" in joined
    ), f"cassette miss did not surface a recording hint: {joined!r}"
