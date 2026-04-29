"""Pydantic v2 models shared across the pipeline — facets, candidates, synthesis output."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel


class PerspectiveScore(BaseModel):
    """A single similarity-perspective score (0-10) with the LLM's rationale."""

    score: float
    rationale: str


class SimilarityScores(BaseModel):
    """Closed set of similarity perspectives the reranker scores against."""

    business_model: PerspectiveScore
    market: PerspectiveScore
    gtm: PerspectiveScore
    stage_scale: PerspectiveScore


class Facets(BaseModel):
    """Facets extracted from an input pitch — the closed-key half pins taxonomy schema."""

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
    """The synthesized post-mortem analogue the LLM emits per candidate."""

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
    """Persisted candidate doc — body, facets, provenance, and text id."""

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
    """A retrieval hit — canonical id + retrieval score + the persisted payload."""

    canonical_id: str
    score: float
    payload: CandidatePayload
    alias_canonicals: list[str] = []


class InputContext(BaseModel):
    """The user's pitch under analysis — name, description, optional recency filter."""

    name: str
    description: str
    years_filter: int | None = None


class ScoredCandidate(BaseModel):
    """LLM rerank output for a single candidate — perspective scores + free-text rationale."""

    candidate_id: str
    perspective_scores: SimilarityScores
    rationale: str


class LlmRerankResult(BaseModel):
    """Wrapper for the rerank stage's array output, so the schema is a single object."""

    ranked: list[ScoredCandidate]


class MergeState(StrEnum):
    """Lifecycle of an entity-resolution merge between two candidates."""

    PENDING = "pending"
    COMPLETE = "complete"
    ALIAS_BLOCKED = "alias_blocked"
    RESOLVER_FLIPPED = "resolver_flipped"


class PipelineMeta(BaseModel):
    """Run metadata pinned to the final ``Report`` for cost/latency/budget bookkeeping."""

    K_retrieve: int
    N_synthesize: int
    models: dict[str, str]
    cost_usd_total: float
    latency_ms_total: int
    trace_id: str | None
    budget_remaining_usd: float
    budget_exceeded: bool


class Report(BaseModel):
    """The user-visible output — input echo, synthesized candidates, and pipeline meta."""

    input: InputContext
    generated_at: datetime
    candidates: list[Synthesis]
    pipeline_meta: PipelineMeta


class ToolSpec(BaseModel):
    """Spec for an LLM-callable tool — name, description, arg model, and async impl."""

    name: str
    description: str
    args_model: type[BaseModel]
    fn: Callable[..., Awaitable[Any]]  # type: ignore[explicit-any]  # tools return varied payloads
    model_config = {"arbitrary_types_allowed": True}


class RawEntry(BaseModel):
    """A scraped raw document before canonicalization — source attribution + bytes."""

    source: str
    source_id: str
    url: str | None
    raw_html: str | None = None
    markdown_text: str | None = None
    fetched_at: datetime
