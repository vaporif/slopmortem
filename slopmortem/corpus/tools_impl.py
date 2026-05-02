"""Tool implementations exposed to the LLM via OpenRouter function-calling.

The corpus tools (``_get_post_mortem`` / ``_search_corpus``) delegate to a
module-level :class:`Corpus` bound at CLI startup via :func:`_set_corpus`.
The indirection keeps tool functions as plain ``async def`` so they match the
:class:`ToolSpec` signature contract (no closures, no bound methods).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from pydantic import BaseModel

from slopmortem.http import safe_post
from slopmortem.models import (
    BusinessModelLit,
    CustomerTypeLit,
    GeographyLit,
    MonetizationLit,
    SectorLit,
    ToolSpec,
)

if TYPE_CHECKING:
    from slopmortem.corpus._store import Corpus

_TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"
_TAVILY_SNIPPET_CHARS = 500

__all__ = [
    "GetPostMortemArgs",
    "SearchCorpusArgs",
    "SearchFacets",
    "SearchHit",
    "TavilyExtractArgs",
    "TavilySearchArgs",
    "_set_corpus",
    "get_post_mortem",
    "search_corpus",
    "set_query_corpus",
    "tavily_extract",
    "tavily_search",
]


class GetPostMortemArgs(BaseModel):
    """Arguments for ``get_post_mortem``: canonical id and read budget."""

    canonical_id: str
    max_chars: int = 8000


class SearchFacets(BaseModel):
    """Closed-enum filters for ``search_corpus``; all optional.

    Values come from ``taxonomy.yml`` at module load (via ``*Lit``), so the
    JSON schema carries an ``enum`` constraint per field. Anthropic's
    grammar-constrained sampler / OpenAI strict tools mode enforces validity
    at decode time — the model can't emit a typo or an out-of-taxonomy value.
    """

    sector: SectorLit | None = None
    business_model: BusinessModelLit | None = None
    customer_type: CustomerTypeLit | None = None
    geography: GeographyLit | None = None
    monetization: MonetizationLit | None = None


class SearchCorpusArgs(BaseModel):
    """Arguments for ``search_corpus``: query string and optional facet filters."""

    q: str
    facets: SearchFacets | None = None
    limit: int = 5


class SearchHit(BaseModel):
    """A single corpus search result with the minimal fields the LLM reasons about."""

    canonical_id: str
    name: str
    snippet: str
    score: float


class TavilySearchArgs(BaseModel):
    """Arguments for ``tavily_search``: web search query string."""

    q: str
    limit: int = 5


class TavilyExtractArgs(BaseModel):
    """Arguments for ``tavily_extract``: URL to fetch and extract."""

    url: str


_corpus: Corpus | None = None


def _set_corpus(c: Corpus) -> None:
    """Bind the module-level :class:`Corpus` used by ``_get_post_mortem`` / ``_search_corpus``.

    Called once at CLI startup so tool functions stay plain ``async def``.
    Tests pass a fake and reset to ``None`` in teardown.
    """
    global _corpus  # noqa: PLW0603 — the module-level binding is the public init surface
    _corpus = c


def set_query_corpus(c: Corpus) -> None:
    """Bind the corpus the query-side LLM tools should call into.

    Public re-export of the module-private ``_set_corpus``. Required so
    callers (``cli.py``, ``evals/runner.py``, ``evals/recording_helper.py``)
    can avoid reaching past the ``corpus`` package façade.
    """
    _set_corpus(c)


async def _get_post_mortem(canonical_id: str, max_chars: int = 8000) -> str:
    if _corpus is None:
        msg = "corpus not initialized"
        raise RuntimeError(msg)
    body = await _corpus.get_post_mortem(canonical_id)
    if max_chars > 0 and len(body) > max_chars:
        return body[:max_chars] + f"\n\n[...truncated; full body is {len(body)} chars...]"
    return body


async def _search_corpus(
    q: str, facets: dict[str, str] | None = None, limit: int = 5
) -> list[SearchHit]:
    if _corpus is None:
        msg = "corpus not initialized"
        raise RuntimeError(msg)
    raw = await _corpus.search_corpus(q, facets=facets)
    hits: list[SearchHit] = []
    for row in raw[:limit]:
        # Corpus.search_corpus returns list[dict[str, Any]]. Impls vary
        # (Qdrant payload shapes, fakes, future stores), so per-row dict
        # values are deliberately Any. Coerce each field to its expected
        # scalar type at this boundary.
        summary = row.get("summary") or row.get("body") or ""
        snippet = str(summary)[:500]
        hits.append(
            SearchHit(
                canonical_id=str(row.get("canonical_id", "")),  # pyright: ignore[reportAny]
                name=str(row.get("name", "")),  # pyright: ignore[reportAny]
                snippet=snippet,
                score=float(row.get("score", 0.0)),  # pyright: ignore[reportAny]
            )
        )
    return hits


def _tavily_api_key() -> str:
    """Return ``TAVILY_API_KEY`` from the environment, or raise.

    Read at call time (rather than from :class:`Config`) because the tool
    callables are passed bare to OpenRouter's function-calling surface and
    the existing ``_set_corpus`` indirection would not extend cleanly to a
    second binding. ``TAVILY_API_KEY`` is the documented surface in the
    spec (Auth, Synthesis tool registry).
    """
    key = os.environ.get("TAVILY_API_KEY", "")
    if not key:
        msg = "TAVILY_API_KEY not set; --tavily-synthesis path is unavailable"
        raise RuntimeError(msg)
    return key


async def _tavily_search(q: str, limit: int = 5) -> str:
    r"""Search the live web via Tavily and return a text summary for the LLM.

    Reads ``TAVILY_API_KEY`` from the environment at call time. Returns a
    newline-joined ``- title — url\n  snippet`` listing, capped at
    *limit* results, or ``"(no results)"`` if Tavily returned an empty set.
    """
    resp = await safe_post(
        _TAVILY_SEARCH_URL,
        json={"api_key": _tavily_api_key(), "query": q, "max_results": limit},
    )
    resp.raise_for_status()
    payload = resp.json()  # pyright: ignore[reportAny]  # httpx Response.json() is Any by design
    raw_hits: list[dict[str, object]] = (
        payload.get("results", [])[:limit] if payload else []  # pyright: ignore[reportAny]
    )
    lines: list[str] = []
    for hit in raw_hits:
        title = str(hit.get("title", "(no title)"))
        url = str(hit.get("url", ""))
        snippet = str(hit.get("content") or "")[:_TAVILY_SNIPPET_CHARS]
        lines.append(f"- {title} — {url}\n  {snippet}")
    return "\n".join(lines) if lines else "(no results)"


async def _tavily_extract(url: str) -> str:
    """Fetch and extract the readable text of a single URL via Tavily.

    Reads ``TAVILY_API_KEY`` from the environment at call time. Returns
    the first result's ``raw_content`` string, or ``""`` if Tavily
    returned no results.
    """
    resp = await safe_post(
        TAVILY_EXTRACT_URL,
        json={"api_key": _tavily_api_key(), "urls": [url]},
    )
    resp.raise_for_status()
    payload = resp.json()  # pyright: ignore[reportAny]  # httpx Response.json() is Any by design
    results: list[dict[str, object]] = (
        payload.get("results", []) if payload else []  # pyright: ignore[reportAny]
    )
    if not results:
        return ""
    return str(results[0].get("raw_content", ""))


get_post_mortem = ToolSpec(
    name="get_post_mortem",
    description=(
        "Fetch the canonical post-mortem text for a candidate, truncated to "
        "max_chars (default 8000). If truncated, the response ends with a "
        "marker indicating the full length; raise max_chars to read more."
    ),
    args_model=GetPostMortemArgs,
    fn=_get_post_mortem,
)

search_corpus = ToolSpec(
    name="search_corpus",
    description=(
        "Search the corpus for additional dead startups matching a query and "
        "optional taxonomy facets (sector, business_model, customer_type, "
        "geography, monetization). Facet values are closed enums — see the "
        "schema for allowed values."
    ),
    args_model=SearchCorpusArgs,
    fn=_search_corpus,
)

tavily_search = ToolSpec(
    name="tavily_search",
    description="Search the live web via Tavily for evidence to support synthesis.",
    args_model=TavilySearchArgs,
    fn=_tavily_search,
)

tavily_extract = ToolSpec(
    name="tavily_extract",
    description="Fetch and extract the readable content of a single URL via Tavily.",
    args_model=TavilyExtractArgs,
    fn=_tavily_extract,
)
