from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel


class PerspectiveScore(BaseModel):
    score: float
    rationale: str


class SimilarityScores(BaseModel):
    business_model: PerspectiveScore
    market: PerspectiveScore
    gtm: PerspectiveScore
    stage_scale: PerspectiveScore


class Facets(BaseModel):
    sector: str
    business_model: str
    customer_type: str
    geography: str
    monetization: str
    sub_sector: str | None = None
    product_type: str | None = None
    price_point: str | None = None
    founding_year: int | None = None
    failure_year: int | None = None


class Synthesis(BaseModel):
    candidate_id: str
    name: str
    one_liner: str
    failure_date: date | None
    lifespan_months: int | None
    similarity: SimilarityScores
    why_similar: str
    where_diverged: str
    failure_causes: list[str]
    lessons_for_input: list[str]
    sources: list[str]


class CandidatePayload(BaseModel):
    name: str
    summary: str
    body: str
    facets: Facets
    founding_date: date | None
    failure_date: date | None
    founding_date_unknown: bool
    failure_date_unknown: bool
    provenance: Literal["curated_real", "scraped"]
    slop_score: float
    sources: list[str]
    text_id: str


class Candidate(BaseModel):
    canonical_id: str
    score: float
    payload: CandidatePayload
    alias_canonicals: list[str] = []


class InputContext(BaseModel):
    name: str
    description: str
    years_filter: int | None = None


class ScoredCandidate(BaseModel):
    candidate_id: str
    perspective_scores: SimilarityScores
    rationale: str


class LlmRerankResult(BaseModel):
    ranked: list[ScoredCandidate]


class MergeState(StrEnum):
    PENDING = "pending"
    COMPLETE = "complete"
    ALIAS_BLOCKED = "alias_blocked"
    RESOLVER_FLIPPED = "resolver_flipped"


class PipelineMeta(BaseModel):
    K_retrieve: int
    N_synthesize: int
    models: dict[str, str]
    cost_usd_total: float
    latency_ms_total: int
    trace_id: str | None
    budget_remaining_usd: float
    budget_exceeded: bool


class Report(BaseModel):
    input: InputContext
    generated_at: datetime
    candidates: list[Synthesis]
    pipeline_meta: PipelineMeta


class ToolSpec(BaseModel):
    name: str
    description: str
    args_model: type[BaseModel]
    fn: Callable[..., Any]
    model_config = {"arbitrary_types_allowed": True}


class RawEntry(BaseModel):
    source: str
    source_id: str
    url: str | None
    raw_html: str | None = None
    markdown_text: str | None = None
    fetched_at: datetime
