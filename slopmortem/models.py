"""Pydantic v2 models shared across the pipeline: facets, candidates, synthesis output."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import date, datetime
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field

# Closed-enum facet fields whose values MUST appear in ``taxonomy.yml``.
# Free-form fields (sub_sector, product_type, price_point, founding_year,
# failure_year) deliberately stay open and are not enum-validated.
_CLOSED_FACET_FIELDS: tuple[str, ...] = (
    "sector",
    "business_model",
    "customer_type",
    "geography",
    "monetization",
)

_TAXONOMY_PATH = Path(__file__).resolve().parent / "corpus" / "taxonomy.yml"


@cache
def _load_taxonomy() -> dict[str, frozenset[str]]:
    """Load ``taxonomy.yml`` once, returning each closed-enum field as a frozenset."""
    # yaml.safe_load is loosely typed; we narrow at the dict boundary, same as
    # slopmortem.corpus.sources.curated.
    raw = cast(
        "dict[str, list[Any]]",  # pyright: ignore[reportExplicitAny]
        yaml.safe_load(_TAXONOMY_PATH.read_text()),
    )
    return {field: frozenset(raw[field]) for field in _CLOSED_FACET_FIELDS}


# Dynamic Literal types built from taxonomy.yml at module-load time. Pydantic v2
# emits the values as ``"enum": [...]`` in the JSON schema, which Anthropic's
# grammar-constrained sampler then enforces, eliminating hallucinations like
# ``geography="japan"`` (Haiku used to invent country names instead of the
# regional ``apac`` bucket) or ``customer_type="b2c"`` (instead of the
# taxonomy's ``consumer``).
_TAX = _load_taxonomy()
_SECTOR_VALUES: tuple[str, ...] = tuple(sorted(_TAX["sector"]))
_BUSINESS_MODEL_VALUES: tuple[str, ...] = tuple(sorted(_TAX["business_model"]))
_CUSTOMER_TYPE_VALUES: tuple[str, ...] = tuple(sorted(_TAX["customer_type"]))
_GEOGRAPHY_VALUES: tuple[str, ...] = tuple(sorted(_TAX["geography"]))
_MONETIZATION_VALUES: tuple[str, ...] = tuple(sorted(_TAX["monetization"]))

# Pydantic introspects the *runtime* Literal to emit JSON-schema ``enum``
# constraints; basedpyright can't expand a tuple at type-check time, so we
# fall back to ``str`` for static analysis. The runtime Literal still enforces
# the closed set via Pydantic's validator.
if TYPE_CHECKING:
    SectorLit = str
    BusinessModelLit = str
    CustomerTypeLit = str
    GeographyLit = str
    MonetizationLit = str
else:
    SectorLit = Literal[*_SECTOR_VALUES]
    BusinessModelLit = Literal[*_BUSINESS_MODEL_VALUES]
    CustomerTypeLit = Literal[*_CUSTOMER_TYPE_VALUES]
    GeographyLit = Literal[*_GEOGRAPHY_VALUES]
    MonetizationLit = Literal[*_MONETIZATION_VALUES]


class PerspectiveScore(BaseModel):
    """One similarity-perspective score (0-10) with the LLM's rationale."""

    score: float
    rationale: str


class SimilarityScores(BaseModel):
    """Closed set of similarity perspectives the reranker scores against."""

    business_model: PerspectiveScore
    market: PerspectiveScore
    gtm: PerspectiveScore
    stage_scale: PerspectiveScore


class Facets(BaseModel):
    """Facets extracted from an input pitch. The closed-key half pins the taxonomy schema.

    Closed enums are typed as ``Literal[*taxonomy_values]`` so Pydantic emits a
    JSON-schema ``enum`` constraint, which Anthropic's grammar-constrained
    sampler then enforces. The post-hoc validator that previously coerced
    out-of-taxonomy values to ``"other"`` is gone; values that aren't in the
    enum can no longer reach this class because the LLM can't generate them.
    """

    sector: SectorLit
    business_model: BusinessModelLit
    customer_type: CustomerTypeLit
    geography: GeographyLit
    monetization: MonetizationLit
    sub_sector: str | None = None
    product_type: str | None = None
    price_point: str | None = None
    founding_year: int | None = None
    failure_year: int | None = None


class LLMSynthesis(BaseModel):
    """The fields the LLM emits for one candidate.

    `failure_date` and `lifespan_months` are deliberately absent: those are
    derived deterministically from the candidate's `CandidatePayload` in
    :func:`slopmortem.stages.synthesize.synthesize`, not asked of the LLM
    (which used to fabricate or mis-extract them from prose).
    """

    candidate_id: str
    name: str
    one_liner: str
    similarity: SimilarityScores
    why_similar: str
    where_diverged: str
    failure_causes: list[str]
    lessons_for_input: list[str]
    sources: list[str]


class Synthesis(BaseModel):
    """The synthesized post-mortem analogue per candidate.

    Composed of the LLM-emitted fields (:class:`LLMSynthesis`) plus
    `failure_date` and `lifespan_months`, which are derived from the
    candidate's typed payload dates rather than re-extracted from prose.
    """

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

    @classmethod
    def from_llm(
        cls,
        llm_synth: LLMSynthesis,
        *,
        founding_date: date | None,
        failure_date: date | None,
    ) -> Synthesis:
        """Build a :class:`Synthesis` from the LLM's output plus typed payload dates.

        `failure_date` is taken straight from the payload (the LLM does not
        see it). `lifespan_months` is the integer month delta between
        `founding_date` and `failure_date`; ``None`` when either is missing
        or the delta is negative (corpus error).
        """
        lifespan = _months_between(founding_date, failure_date)
        return cls(
            candidate_id=llm_synth.candidate_id,
            name=llm_synth.name,
            one_liner=llm_synth.one_liner,
            failure_date=failure_date,
            lifespan_months=lifespan,
            similarity=llm_synth.similarity,
            why_similar=llm_synth.why_similar,
            where_diverged=llm_synth.where_diverged,
            failure_causes=llm_synth.failure_causes,
            lessons_for_input=llm_synth.lessons_for_input,
            sources=llm_synth.sources,
        )


def _months_between(founding: date | None, failure: date | None) -> int | None:
    """Whole months between two dates, or ``None`` if either is missing or the delta is negative."""
    if founding is None or failure is None:
        return None
    months = (failure.year - founding.year) * 12 + (failure.month - founding.month)
    return months if months >= 0 else None


class CandidatePayload(BaseModel):
    """Persisted candidate doc: body, facets, provenance, and text id.

    ``sources`` is URL-only (may be empty when the upstream entry had no URL,
    e.g. CSV imports). ``provenance_id`` is the synthetic ``"<source>:<source_id>"``
    audit string that always identifies where the doc came from. The previous
    behavior — falling back to the synthetic id inside ``sources`` — broke the
    synth-stage host allowlist, which silently dropped every cited URL because
    ``urlparse("curated:Celsius Network").hostname is None``.
    """

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
    provenance_id: str = ""
    text_id: str


class Candidate(BaseModel):
    """A retrieval hit: canonical id, retrieval score, and the persisted payload."""

    canonical_id: str
    score: float
    payload: CandidatePayload
    alias_canonicals: list[str] = Field(default_factory=list)


class InputContext(BaseModel):
    """The user's pitch under analysis: name, description, and an optional recency filter."""

    name: str
    description: str
    years_filter: int | None = None


class ScoredCandidate(BaseModel):
    """LLM rerank output for a single candidate: perspective scores plus a free-text rationale."""

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
    """Run metadata attached to the final ``Report`` for cost, latency, and budget bookkeeping."""

    K_retrieve: int
    N_synthesize: int
    models: dict[str, str]
    cost_usd_total: float
    latency_ms_total: int
    trace_id: str | None
    budget_remaining_usd: float
    budget_exceeded: bool


class TopRisk(BaseModel):
    """One cluster of similar lessons across candidates.

    Attributes:
        summary: A short canonical statement of the lesson (the shortest member
            of the cluster, in original casing).
        candidate_ids: Which candidates raised this lesson (deduped, ordered as
            encountered while iterating over syntheses).
        frequency: ``len(candidate_ids)``; a denormalized convenience for
            renderers so they don't have to recompute it.
    """

    summary: str
    candidate_ids: list[str]
    frequency: int


class TopRisks(BaseModel):
    """Cross-candidate dedup of lessons, ranked by frequency descending."""

    clusters: list[TopRisk] = Field(default_factory=list)


class Report(BaseModel):
    """The user-visible output: input echo, synthesized candidates, and pipeline meta."""

    input: InputContext
    generated_at: datetime
    candidates: list[Synthesis]
    pipeline_meta: PipelineMeta
    top_risks: TopRisks = Field(default_factory=TopRisks)


class ToolSpec(BaseModel):
    """Spec for an LLM-callable tool: name, description, arg model, and async impl."""

    name: str
    description: str
    args_model: type[BaseModel]
    fn: Callable[..., Awaitable[Any]]  # pyright: ignore[reportExplicitAny]  # tools return varied payloads
    model_config = ConfigDict(arbitrary_types_allowed=True)


class RawEntry(BaseModel):
    """A scraped raw document before canonicalization: source attribution plus bytes."""

    source: str
    source_id: str
    url: str | None
    raw_html: str | None = None
    markdown_text: str | None = None
    fetched_at: datetime


class AliasEdge(BaseModel):
    """Edge in the alias graph: links a canonical to a parent, acquirer, or rebrand target."""

    canonical_id: str
    alias_kind: Literal["acquired_by", "rebranded_to", "pivoted_from", "parent_of", "subsidiary_of"]
    target_canonical_id: str
    evidence_source_id: str
    confidence: float


class PendingReviewRow(BaseModel):
    """A row in the entity-resolution ``pending_review`` queue (spec line 264)."""

    pair_key: str
    similarity_score: float | None
    haiku_decision: str | None
    haiku_rationale: str | None
    raw_section_heads: str | None


class ReclassifyReport(BaseModel):
    """Result of a ``slopmortem ingest --reclassify`` pass (spec line 252)."""

    total: int
    declassified: int
    still_slop: int
    errors: int
