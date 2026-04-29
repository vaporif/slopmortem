"""Wayback enricher — recovers content for curated rows whose live URL is dead.

* No-op when ``raw_html`` is already populated.
* When ``raw_html`` is empty, hit Wayback's availability API, fetch the snapshot
  URL it returns, and stash the result in ``raw_html`` + ``markdown_text``.
"""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from slopmortem.corpus.sources.wayback import WaybackEnricher
from slopmortem.models import RawEntry


class _FakeResp:
    def __init__(
        self,
        *,
        text: str = "",
        json_payload: dict[str, Any] | None = None,
        status: int = 200,
    ) -> None:
        self.text = text
        self.status_code = status
        self._json = json_payload or {}

    def json(self) -> dict[str, Any]:
        return self._json


@pytest.mark.asyncio
async def test_wayback_noop_when_raw_html_present(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = AsyncMock()
    monkeypatch.setattr("slopmortem.corpus.sources.wayback.safe_get", fake)
    entry = RawEntry(
        source="curated",
        source_id="acme",
        url="https://acme.example/post",
        raw_html="<html><body>existing</body></html>",
        markdown_text=None,
        fetched_at=datetime.now(UTC),
    )
    enr = WaybackEnricher()
    out = await enr.enrich(entry)
    # No HTTP triggered; raw_html unchanged.
    assert fake.call_count == 0
    assert out.raw_html == entry.raw_html


def _long_body(seed: str) -> str:
    body = f"{seed} " + ("padding " * 250)
    return f"<html><body><p>{body}</p></body></html>"


@pytest.mark.asyncio
async def test_wayback_fetches_snapshot_when_html_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_url = "https://web.archive.org/web/20230101000000/https://acme.example/post"
    availability_payload = {
        "archived_snapshots": {
            "closest": {
                "available": True,
                "url": snapshot_url,
                "timestamp": "20230101000000",
                "status": "200",
            }
        }
    }
    snapshot_html = _long_body("ACME ARCHIVED CONTENT")
    responses = {
        # Availability API call
        "https://archive.org/wayback/available?url=https%3A%2F%2Facme.example%2Fpost": _FakeResp(
            json_payload=availability_payload
        ),
        snapshot_url: _FakeResp(text=snapshot_html),
    }

    async def fake_get(url: str, **_kw: object) -> Any:
        if url not in responses:
            msg = f"unexpected URL: {url}"
            raise AssertionError(msg)
        return responses[url]

    monkeypatch.setattr("slopmortem.corpus.sources.wayback.safe_get", fake_get)
    monkeypatch.setattr(
        "slopmortem.corpus.sources.wayback.respect_robots",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "slopmortem.corpus.sources.wayback.throttle_for",
        AsyncMock(return_value=None),
    )

    entry = RawEntry(
        source="curated",
        source_id="acme",
        url="https://acme.example/post",
        raw_html=None,
        markdown_text=None,
        fetched_at=datetime.now(UTC),
    )
    enr = WaybackEnricher()
    out = await enr.enrich(entry)
    assert out.raw_html == snapshot_html
    assert out.markdown_text is not None
    assert "ACME ARCHIVED CONTENT" in out.markdown_text


@pytest.mark.asyncio
async def test_wayback_returns_entry_unchanged_when_no_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"archived_snapshots": {}}
    monkeypatch.setattr(
        "slopmortem.corpus.sources.wayback.safe_get",
        AsyncMock(return_value=_FakeResp(json_payload=payload)),
    )
    monkeypatch.setattr(
        "slopmortem.corpus.sources.wayback.respect_robots",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "slopmortem.corpus.sources.wayback.throttle_for",
        AsyncMock(return_value=None),
    )

    entry = RawEntry(
        source="curated",
        source_id="acme",
        url="https://acme.example/post",
        raw_html=None,
        markdown_text=None,
        fetched_at=datetime.now(UTC),
    )
    enr = WaybackEnricher()
    out = await enr.enrich(entry)
    assert out.raw_html is None
    assert out.markdown_text is None
