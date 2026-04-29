"""Tool implementations exposed to the LLM via OpenRouter function-calling.

The corpus tools (``_get_post_mortem`` / ``_search_corpus``) delegate to a
module-level :class:`Corpus` bound at CLI startup via :func:`_set_corpus`.
The indirection keeps the tool functions as plain ``async def`` so they
match the :class:`ToolSpec` signature contract (no closures, no bound
methods).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from slopmortem.models import ToolSpec

if TYPE_CHECKING:
    from slopmortem.corpus.store import Corpus

__all__ = [
    "GetPostMortemArgs",
    "SearchCorpusArgs",
    "SearchHit",
    "TavilyExtractArgs",
    "TavilySearchArgs",
    "_set_corpus",
    "get_post_mortem",
    "search_corpus",
    "tavily_extract",
    "tavily_search",
]


class GetPostMortemArgs(BaseModel):
    """Arguments for ``get_post_mortem``: canonical id of the candidate."""

    canonical_id: str


class SearchCorpusArgs(BaseModel):
    """Arguments for ``search_corpus``: query string + optional facet filters."""

    q: str
    facets: dict[str, str] | None = None
    limit: int = 5


class SearchHit(BaseModel):
    """A single corpus search result; minimal fields the LLM needs to reason about."""

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
    Tests pass a fake here and reset to ``None`` in teardown.
    """
    global _corpus  # noqa: PLW0603 — the module-level binding is the public init surface
    _corpus = c


async def _get_post_mortem(canonical_id: str) -> str:
    if _corpus is None:
        msg = "corpus not initialized"
        raise RuntimeError(msg)
    return await _corpus.get_post_mortem(canonical_id)


async def _search_corpus(
    q: str, facets: dict[str, str] | None = None, limit: int = 5
) -> list[SearchHit]:
    if _corpus is None:
        msg = "corpus not initialized"
        raise RuntimeError(msg)
    raw = await _corpus.search_corpus(q, facets=facets)
    hits: list[SearchHit] = []
    for row in raw[:limit]:
        # Corpus.search_corpus returns list[dict[str, Any]]; impls vary
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


async def _tavily_search(q: str, limit: int = 5) -> str:
    _ = (q, limit)
    msg = "Task #11"
    raise NotImplementedError(msg)


async def _tavily_extract(url: str) -> str:
    _ = url
    msg = "Task #11"
    raise NotImplementedError(msg)


get_post_mortem = ToolSpec(
    name="get_post_mortem",
    description="Fetch the full canonical post-mortem text for a candidate.",
    args_model=GetPostMortemArgs,
    fn=_get_post_mortem,
)

search_corpus = ToolSpec(
    name="search_corpus",
    description=(
        "Search the corpus for additional dead startups matching a query and optional facets."
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
