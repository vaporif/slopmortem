"""HN Algolia source — chronological obituary coverage via the Algolia REST API.

Endpoint is pinned to ``/api/v1/search_by_date`` (chronological, newest-first)
rather than ``/search`` (relevance-ranked) — see spec line 242. Relevance
ranking would re-surface the same long-tail popular threads on every ingest.

Query params per spec:
* ``tags=story``
* ``query=<term>``
* ``numericFilters=created_at_i>=<since-epoch>`` for incremental ingest
* paginated via ``page=<n>`` until ``nbPages`` is exhausted

All HTTP funnels through :func:`safe_get` (SSRF-hardened) with the configured
``slopmortem/<version> (+<repo>)`` UA, gated by the per-host throttle and
robots.txt check from :mod:`._throttle`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote_plus

from slopmortem.corpus.sources._throttle import (
    HTTP_BAD_REQUEST,
    USER_AGENT,
    respect_robots,
    throttle_for,
)
from slopmortem.http import safe_get
from slopmortem.models import RawEntry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

ENDPOINT = "https://hn.algolia.com/api/v1/search_by_date"


class HNAlgoliaSource:
    """[Source] HN Algolia REST client, paginated by ``nbPages``."""

    def __init__(
        self,
        *,
        query: str,
        since_epoch: int | None = None,
        user_agent: str = USER_AGENT,
        rps: float = 1.0,
    ) -> None:
        """Build an HN Algolia source.

        Args:
            query: Search term passed as ``query=``.
            since_epoch: Optional ``created_at_i>=`` lower bound for incremental ingest.
            user_agent: UA string sent on outbound requests.
            rps: Per-host throttle budget; defaults to 1 request/second.
        """
        self.query = query
        self.since_epoch = since_epoch
        self.user_agent = user_agent
        self.rps = rps

    def build_url(self, *, page: int) -> str:
        """Construct the request URL for *page*.

        Args:
            page: Zero-based page index.

        Returns:
            Fully-qualified URL pointing at the ``search_by_date`` endpoint.
        """
        params = [
            f"query={quote_plus(self.query)}",
            "tags=story",
            f"page={page}",
        ]
        if self.since_epoch is not None:
            # numericFilters=created_at_i>=<epoch> — the >= must be URL-encoded.
            params.append(f"numericFilters={quote_plus(f'created_at_i>={self.since_epoch}')}")
        return f"{ENDPOINT}?{'&'.join(params)}"

    @staticmethod
    def _hit_to_entry(
        hit: dict[str, Any],  # pyright: ignore[reportExplicitAny] — Algolia payload
    ) -> RawEntry | None:
        object_id: object = hit.get("objectID")
        if not isinstance(object_id, str) or not object_id:
            return None
        url_field: object = hit.get("url")
        url = url_field if isinstance(url_field, str) and url_field else None
        title: object = hit.get("title") or ""
        body: object = hit.get("story_text") or hit.get("comment_text") or ""
        markdown_text = f"# {title}\n\n{body}".strip()
        return RawEntry(
            source="hn_algolia",
            source_id=object_id,
            url=url,
            raw_html=None,
            markdown_text=markdown_text or None,
            fetched_at=datetime.now(UTC),
        )

    async def fetch(self) -> AsyncIterator[RawEntry]:
        """Yield ``RawEntry`` per ``hits`` row across every page until ``nbPages``."""
        page = 0
        while True:
            url = self.build_url(page=page)
            if not await respect_robots(url, user_agent=self.user_agent):
                logger.info("hn_algolia: robots blocked %s", url)
                return
            await throttle_for(url, rps=self.rps)
            resp = await safe_get(url)
            if resp.status_code >= HTTP_BAD_REQUEST:
                logger.warning("hn_algolia: HTTP %s for %s", resp.status_code, url)
                return
            # Algolia returns a JSON object; we narrow with isinstance below.
            payload = cast(
                "dict[str, Any]",  # pyright: ignore[reportExplicitAny]
                resp.json(),
            )
            hits_field: object = payload.get("hits") or []
            if not isinstance(hits_field, list):
                logger.warning("hn_algolia: unexpected hits type for %s", url)
                return
            hits_list = cast("list[object]", hits_field)
            for hit in hits_list:
                if not isinstance(hit, dict):
                    continue
                entry = self._hit_to_entry(cast("dict[str, Any]", hit))  # pyright: ignore[reportExplicitAny]
                if entry is not None:
                    yield entry
            nb_pages_field: object = payload.get("nbPages")
            nb_pages = nb_pages_field if isinstance(nb_pages_field, int) else 0
            page += 1
            if page >= nb_pages:
                return
