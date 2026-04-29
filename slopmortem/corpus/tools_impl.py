"""Tool implementations exposed to the LLM via OpenRouter function-calling."""

from __future__ import annotations

from pydantic import BaseModel

from slopmortem.models import ToolSpec


class GetPostMortemArgs(BaseModel):
    """Arguments for the ``get_post_mortem`` tool — canonical id of the candidate."""

    canonical_id: str


class SearchCorpusArgs(BaseModel):
    """Arguments for the ``search_corpus`` tool — query string + optional facet filters."""

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
    """Arguments for the ``tavily_search`` tool — web search query string."""

    q: str
    limit: int = 5


class TavilyExtractArgs(BaseModel):
    """Arguments for the ``tavily_extract`` tool — URL to fetch and extract."""

    url: str


async def _get_post_mortem(canonical_id: str) -> str:
    _ = canonical_id
    msg = "Task #9"
    raise NotImplementedError(msg)


async def _search_corpus(
    q: str, facets: dict[str, str] | None = None, limit: int = 5
) -> list[SearchHit]:
    _ = (q, facets, limit)
    msg = "Task #9"
    raise NotImplementedError(msg)


async def _tavily_search(q: str, limit: int = 5) -> str:
    _ = (q, limit)
    msg = "Task #9"
    raise NotImplementedError(msg)


async def _tavily_extract(url: str) -> str:
    _ = url
    msg = "Task #9"
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
