"""Curated source: length floor, happy path.

The curated YAML loader is the entry point for hand-vetted post-mortems. It
fetches each row's URL via ``safe_get``, runs it through ``extract_clean``,
and skips rows whose extracted text falls under the 500-char floor.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from slopmortem.corpus.sources.curated import CuratedSource

if TYPE_CHECKING:
    from slopmortem.models import RawEntry

FIXTURE = Path(__file__).parent.parent / "fixtures" / "curated_test.yml"


def _canned_response(url: str, html: str, status: int = 200) -> object:
    class _Resp:
        def __init__(self) -> None:
            self.status_code = status
            self.text = html
            self.url = url

        def raise_for_status(self) -> None:
            if status >= 400:
                msg = f"HTTP {status}"
                raise RuntimeError(msg)

    return _Resp()


def _long_html(seed: str) -> str:
    body = f"{seed}. " + ("padding " * 250)
    return f"<html><body><p>{body}</p></body></html>"


async def _fake_safe_get_factory(
    response_map: dict[str, object],
) -> AsyncMock:
    async def _fn(url: str, **_kw: object) -> object:
        if url not in response_map:
            msg = f"unexpected URL: {url}"
            raise AssertionError(msg)
        return response_map[url]

    return AsyncMock(side_effect=_fn)


@pytest.mark.asyncio
async def test_curated_yields_long_text_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    response_map = {
        "https://example.com/long-postmortem": _canned_response(
            "https://example.com/long-postmortem",
            _long_html("ExampleCorp post-mortem"),
        ),
        "https://example.org/too-short-page": _canned_response(
            "https://example.org/too-short-page",
            "<html><body><p>too short</p></body></html>",
        ),
        "https://realdomain.example/another-long-postmortem": _canned_response(
            "https://realdomain.example/another-long-postmortem",
            _long_html("RealDomainCo retrospective"),
        ),
    }
    fake_get = await _fake_safe_get_factory(response_map)
    monkeypatch.setattr("slopmortem.corpus.sources.curated.safe_get", fake_get)
    monkeypatch.setattr(
        "slopmortem.corpus.sources.curated.respect_robots",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "slopmortem.corpus.sources.curated.throttle_for",
        AsyncMock(return_value=None),
    )

    src = CuratedSource(yaml_path=FIXTURE)
    entries: list[RawEntry] = [e async for e in src.fetch()]
    urls = {e.url for e in entries}
    # Long-text rows pass through.
    assert "https://example.com/long-postmortem" in urls
    assert "https://realdomain.example/another-long-postmortem" in urls
    # Length floor: too-short row is dropped.
    assert "https://example.org/too-short-page" not in urls


@pytest.mark.asyncio
async def test_curated_entry_has_expected_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://example.com/long-postmortem"
    response_map = {
        url: _canned_response(url, _long_html("ExampleCorp post-mortem")),
        "https://example.org/too-short-page": _canned_response(
            "https://example.org/too-short-page",
            "<html><body><p>too short</p></body></html>",
        ),
        "https://realdomain.example/another-long-postmortem": _canned_response(
            "https://realdomain.example/another-long-postmortem",
            _long_html("RealDomainCo retrospective"),
        ),
    }
    fake_get = await _fake_safe_get_factory(response_map)
    monkeypatch.setattr("slopmortem.corpus.sources.curated.safe_get", fake_get)
    monkeypatch.setattr(
        "slopmortem.corpus.sources.curated.respect_robots",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "slopmortem.corpus.sources.curated.throttle_for",
        AsyncMock(return_value=None),
    )

    src = CuratedSource(yaml_path=FIXTURE)
    entries = [e async for e in src.fetch()]
    target = next(e for e in entries if e.url == url)
    assert target.source == "curated"
    assert target.source_id  # non-empty
    assert target.markdown_text is not None
    assert "ExampleCorp" in target.markdown_text
    assert isinstance(target.fetched_at, datetime)
    # fetched_at is UTC-aware.
    assert target.fetched_at.tzinfo is not None
    assert target.fetched_at.tzinfo.utcoffset(target.fetched_at) == UTC.utcoffset(target.fetched_at)


@pytest.mark.asyncio
async def test_curated_skips_robots_disallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rows whose URL is disallowed by robots.txt are skipped (no fetch)."""
    url = "https://example.com/long-postmortem"
    response_map = {
        url: _canned_response(url, _long_html("ExampleCorp post-mortem")),
        "https://example.org/too-short-page": _canned_response(
            "https://example.org/too-short-page",
            "<html><body><p>too short</p></body></html>",
        ),
        "https://realdomain.example/another-long-postmortem": _canned_response(
            "https://realdomain.example/another-long-postmortem",
            _long_html("RealDomainCo retrospective"),
        ),
    }
    fake_get = await _fake_safe_get_factory(response_map)
    monkeypatch.setattr("slopmortem.corpus.sources.curated.safe_get", fake_get)
    monkeypatch.setattr(
        "slopmortem.corpus.sources.curated.respect_robots",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "slopmortem.corpus.sources.curated.throttle_for",
        AsyncMock(return_value=None),
    )

    src = CuratedSource(yaml_path=FIXTURE)
    entries = [e async for e in src.fetch()]
    assert entries == []
    # No URLs were fetched because robots blocked everything.
    assert fake_get.call_count == 0
