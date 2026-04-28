from __future__ import annotations

from pydantic import BaseModel

from slopmortem.models import ToolSpec


class GetPostMortemArgs(BaseModel):
    canonical_id: str


class SearchCorpusArgs(BaseModel):
    q: str
    facets: dict[str, str] | None = None
    limit: int = 5


class SearchHit(BaseModel):
    canonical_id: str
    name: str
    snippet: str
    score: float


async def _get_post_mortem(canonical_id: str) -> str:
    msg = "Task #9"
    raise NotImplementedError(msg)


async def _search_corpus(
    q: str, facets: dict[str, str] | None = None, limit: int = 5
) -> list[SearchHit]:
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
    description="Search the corpus for additional dead startups matching a query and optional facets.",
    args_model=SearchCorpusArgs,
    fn=_search_corpus,
)
