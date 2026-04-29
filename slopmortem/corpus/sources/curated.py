"""Curated source — load YAML of hand-vetted post-mortem URLs and produce ``RawEntry``.

Pipeline per spec line 244: read the YAML, drop rows whose registrable_domain is
in :file:`platform_domains.yml`, fetch via :func:`safe_get`, run the response
through :func:`extract_clean`, drop any row whose extracted text is below the
length floor, yield a ``RawEntry``.

All outbound HTTP funnels through ``safe_get`` (SSRF-hardened) and is gated by
the per-host token bucket plus ``robots.txt`` parser in
:mod:`slopmortem.corpus.sources._throttle`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

import httpx
import tldextract
import yaml

from slopmortem.corpus.extract import extract_clean
from slopmortem.corpus.sources._throttle import (
    HTTP_BAD_REQUEST,
    USER_AGENT,
    respect_robots,
    throttle_for,
)
from slopmortem.http import SSRFBlockedError, safe_get
from slopmortem.models import RawEntry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

PLATFORM_DOMAINS_YAML = Path(__file__).parent / "platform_domains.yml"


def _load_platform_domains() -> frozenset[str]:
    with PLATFORM_DOMAINS_YAML.open("r", encoding="utf-8") as fh:
        # yaml.safe_load is intentionally loosely typed — we narrow with isinstance below.
        data = cast("dict[str, Any]", yaml.safe_load(fh) or {})  # pyright: ignore[reportExplicitAny]
    domains_obj: object = data.get("domains") or []
    if not isinstance(domains_obj, list):
        return frozenset()
    domains_list = cast("list[object]", domains_obj)
    return frozenset(str(d).lower() for d in domains_list)


def _registrable_domain(url: str) -> str:
    extracted = tldextract.extract(url)
    if not extracted.domain or not extracted.suffix:
        # Fall back to hostname when tldextract can't parse (e.g. raw IP).
        host = urlparse(url).hostname or ""
        return host.lower()
    return f"{extracted.domain}.{extracted.suffix}".lower()


class CuratedSource:
    """[Source] YAML loader that fetches curated post-mortem URLs."""

    def __init__(
        self,
        yaml_path: Path,
        *,
        user_agent: str = USER_AGENT,
        rps: float = 1.0,
    ) -> None:
        """Build a curated source.

        Args:
            yaml_path: Path to the curated YAML file.
            user_agent: UA string sent on outbound requests.
            rps: Per-host throttle budget; defaults to 1 request/second.
        """
        self.yaml_path = yaml_path
        self.user_agent = user_agent
        self.rps = rps
        self._blocked_domains = _load_platform_domains()

    def _load_rows(self) -> list[dict[str, object]]:
        with self.yaml_path.open("r", encoding="utf-8") as fh:
            data: object = yaml.safe_load(fh) or []
        if not isinstance(data, list):
            msg = f"curated YAML at {self.yaml_path} must be a list"
            raise TypeError(msg)
        rows = cast("list[object]", data)
        return [r for r in rows if isinstance(r, dict)]

    async def fetch(self) -> AsyncIterator[RawEntry]:
        """Yield ``RawEntry`` for every YAML row that survives blocklist + length floor."""
        rows = self._load_rows()
        for row in rows:
            url = row.get("url")
            if not isinstance(url, str) or not url:
                logger.debug("curated: skipping row without url: %r", row)
                continue
            domain = _registrable_domain(url)
            if domain in self._blocked_domains:
                logger.info("curated: skipping platform-blocked host %s for %s", domain, url)
                continue
            if not await respect_robots(url, user_agent=self.user_agent):
                logger.info("curated: skipping robots-disallowed url %s", url)
                continue
            await throttle_for(url, rps=self.rps)
            try:
                resp = await safe_get(url)
            except (SSRFBlockedError, httpx.HTTPError) as exc:
                logger.warning("curated: fetch failed for %s: %s", url, exc)
                continue
            if resp.status_code >= HTTP_BAD_REQUEST:
                logger.warning("curated: HTTP %s for %s", resp.status_code, url)
                continue
            text = extract_clean(resp.text)
            if not text:
                logger.info("curated: extracted text below length floor for %s", url)
                continue
            startup_name = row.get("startup_name")
            source_id = str(startup_name) if isinstance(startup_name, str) and startup_name else url
            yield RawEntry(
                source="curated",
                source_id=source_id,
                url=url,
                raw_html=resp.text,
                markdown_text=text,
                fetched_at=datetime.now(UTC),
            )
