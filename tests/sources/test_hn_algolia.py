"""HN Algolia source: URL prefix and RECORD-gated cassette round-trip.

The endpoint must be ``/api/v1/search_by_date`` (chronological, newest-first),
not ``/search`` (relevance-ranked); see spec line 242 and plan §1901. The
URL-prefix test guards against an accidental swap.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from slopmortem.corpus.sources.hn_algolia import HNAlgoliaSource

CASSETTE_FILE = (
    Path(__file__).parent / "cassettes" / "test_hn_algolia" / "test_hn_algolia_round_trip.yaml"
)


def test_constructed_url_starts_with_search_by_date() -> None:
    """Catches accidental swap to relevance-ranked /search endpoint."""
    src = HNAlgoliaSource(query="post-mortem")
    url = src.build_url(page=0)
    assert url.startswith("https://hn.algolia.com/api/v1/search_by_date?"), url
    # never the bare /search prefix
    assert not url.startswith("https://hn.algolia.com/api/v1/search?"), url


def test_constructed_url_includes_required_params() -> None:
    src = HNAlgoliaSource(
        query="post-mortem",
        since_epoch=1_700_000_000,
    )
    url = src.build_url(page=2)
    assert "tags=story" in url
    assert "query=post-mortem" in url
    assert "page=2" in url
    assert "numericFilters=created_at_i%3E%3D1700000000" in url


class _FakeResp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200
        self.text = ""

    def json(self) -> dict[str, Any]:
        return self._payload


async def test_paginates_until_nbpages_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    """A single page (nbPages=1) yields exactly the hits and no further calls."""
    payload = {
        "nbPages": 1,
        "hits": [
            {
                "objectID": "12345",
                "title": "Acme post-mortem",
                "url": "https://acme.example/post",
                "story_text": "We shut down. " * 60,
                "created_at_i": 1_700_000_500,
            },
            {
                "objectID": "12346",
                "title": "Beta shutdown",
                "url": None,
                "story_text": "Self-post body. " * 60,
                "created_at_i": 1_700_000_600,
            },
        ],
    }
    fake = AsyncMock(return_value=_FakeResp(payload))
    monkeypatch.setattr("slopmortem.corpus.sources.hn_algolia.safe_get", fake)
    monkeypatch.setattr(
        "slopmortem.corpus.sources.hn_algolia.respect_robots",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "slopmortem.corpus.sources.hn_algolia.throttle_for",
        AsyncMock(return_value=None),
    )

    src = HNAlgoliaSource(query="post-mortem")
    entries = [e async for e in src.fetch()]
    assert len(entries) == 2
    assert all(e.source == "hn_algolia" for e in entries)
    assert entries[0].source_id == "12345"
    assert entries[0].url == "https://acme.example/post"
    assert isinstance(entries[0].fetched_at, datetime)
    # Single page = one HTTP call.
    assert fake.call_count == 1


async def test_paginates_multiple_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    page0 = {"nbPages": 2, "hits": [{"objectID": "1", "title": "a", "story_text": "x"}]}
    page1 = {"nbPages": 2, "hits": [{"objectID": "2", "title": "b", "story_text": "y"}]}
    responses = [_FakeResp(page0), _FakeResp(page1)]

    async def fake(_url: str, **_kw: object) -> Any:
        return responses.pop(0)

    monkeypatch.setattr("slopmortem.corpus.sources.hn_algolia.safe_get", fake)
    monkeypatch.setattr(
        "slopmortem.corpus.sources.hn_algolia.respect_robots",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "slopmortem.corpus.sources.hn_algolia.throttle_for",
        AsyncMock(return_value=None),
    )

    src = HNAlgoliaSource(query="post-mortem")
    entries = [e async for e in src.fetch()]
    assert {e.source_id for e in entries} == {"1", "2"}


@pytest.mark.vcr
async def test_hn_algolia_round_trip() -> None:
    """RECORD-gated live API round-trip; mirrors the ``test_openrouter_cassette`` pattern."""
    if not CASSETTE_FILE.exists() and not os.environ.get("RECORD"):
        pytest.skip(f"no cassette at {CASSETTE_FILE}; rerun with RECORD=1 to record")
    since = int(datetime(2024, 1, 1, tzinfo=UTC).timestamp())
    src = HNAlgoliaSource(query="post-mortem", since_epoch=since)
    entries = [e async for e in src.fetch()]
    # cassette is small, assert structurally rather than count-precise
    assert all(e.source == "hn_algolia" for e in entries)
