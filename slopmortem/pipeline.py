"""Top-level orchestration: ``run_query`` wires every stage of the slopmortem pipeline.

Pure library code. No I/O, no stderr, no path/file access, no outbound HTTP. Every
side-effecting capability arrives via injected dependencies (LLM client, embedding
client, Corpus, Budget, optional progress callback). The CLI in ``slopmortem.cli``
constructs those dependencies and calls ``run_query``.

Failure model (spec §770-775):
- A :class:`BudgetExceededError` raised by any stage truncates the run. Whatever
  Synthesis values had already accumulated up to that point are still returned in
  the final :class:`Report` with ``budget_exceeded=True``.
- Per-candidate synthesis failures do NOT abort the run. ``synthesize_all`` already
  swallows them as exception entries, and we filter them out of the final list so
  ``Report.candidates`` only carries successful Synthesis values.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from lmnr import Laminar, observe

from slopmortem.budget import BudgetExceededError
from slopmortem.models import PipelineMeta, Report, Synthesis, TopRisks
from slopmortem.stages.consolidate_risks import consolidate_risks
from slopmortem.stages.facet_extract import extract_facets
from slopmortem.stages.llm_rerank import llm_rerank
from slopmortem.stages.retrieve import retrieve
from slopmortem.stages.synthesize import synthesize_all
from slopmortem.tracing import git_sha, mint_run_id
from slopmortem.tracing.events import SpanEvent

if TYPE_CHECKING:
    from slopmortem.budget import Budget
    from slopmortem.config import Config
    from slopmortem.corpus.store import Corpus
    from slopmortem.llm.client import LLMClient
    from slopmortem.llm.embedding_client import EmbeddingClient
    from slopmortem.models import Candidate, InputContext, ScoredCandidate, SimilarityScores
    from slopmortem.stages.retrieve import SparseEncoder


_DAYS_PER_YEAR = 365


class QueryPhase(StrEnum):
    """Closed set of phase keys used by :class:`QueryProgress`.

    Mirrors :class:`slopmortem.ingest.IngestPhase`: a closed enum gives typo
    safety (``"facetextract"`` fails at parse time) and exhaustiveness checks
    in match statements and dict literals.
    """

    FACET_EXTRACT = "facet_extract"
    RETRIEVE = "retrieve"
    RERANK = "rerank"
    SYNTHESIZE = "synthesize"


@runtime_checkable
class QueryProgress(Protocol):
    """Phase-level progress hooks for ``slopmortem query``.

    Methods are no-op-safe: :class:`NullQueryProgress` keeps the orchestrator
    decoupled from any specific UI library, while the CLI wires a Rich-based
    implementation. Mirrors :class:`slopmortem.ingest.IngestProgress` so a
    future shared base is straightforward.
    """

    def start_phase(self, phase: QueryPhase, total: int) -> None:
        """Announce *phase* with an expected ``total`` of advances."""

    def advance_phase(self, phase: QueryPhase, n: int = 1) -> None:
        """Advance *phase*'s bar by ``n``."""

    def end_phase(self, phase: QueryPhase) -> None:
        """Mark *phase* complete."""

    def log(self, message: str) -> None:
        """Emit a one-off status line."""

    def error(self, phase: QueryPhase, message: str) -> None:
        """Record an error against *phase*."""


class NullQueryProgress:
    """No-op :class:`QueryProgress` used when no display surface is attached."""

    def start_phase(self, phase: QueryPhase, total: int) -> None:
        """No-op."""

    def advance_phase(self, phase: QueryPhase, n: int = 1) -> None:
        """No-op."""

    def end_phase(self, phase: QueryPhase) -> None:
        """No-op."""

    def log(self, message: str) -> None:
        """No-op."""

    def error(self, phase: QueryPhase, message: str) -> None:
        """No-op."""


def cutoff_iso(years_filter: int | None) -> str | None:
    """Convert a years-back recency filter into an ISO-8601 date string.

    Retrieve takes ISO-8601 dates (``YYYY-MM-DD``), not full timestamps. Floor
    to ``date()`` here so the cutoff stays stable across the hour the query runs.
    """
    if years_filter is None:
        return None
    return (datetime.now(UTC) - timedelta(days=_DAYS_PER_YEAR * years_filter)).date().isoformat()


def _mean_similarity_score(scores: SimilarityScores) -> float:
    """Mean of the four 0-10 perspective scores."""
    perspectives = [
        scores.business_model.score,
        scores.market.score,
        scores.gtm.score,
        scores.stage_scale.score,
    ]
    return sum(perspectives) / len(perspectives)


def _filter_by_min_similarity(
    ranked: list[ScoredCandidate], threshold: float
) -> list[ScoredCandidate]:
    """Drop reranked candidates whose mean perspective score is below ``threshold``."""
    return [s for s in ranked if _mean_similarity_score(s.perspective_scores) >= threshold]


def _filter_synth_by_min_similarity(
    syntheses: list[Synthesis], threshold: float
) -> list[Synthesis]:
    """Drop syntheses whose mean perspective score is below ``threshold``.

    Synthesis sometimes re-scores a candidate lower than rerank did, so a row
    that cleared the rerank-side filter can still come back below the bar.
    This second pass keeps the rendered table consistent with the threshold.
    """
    return [s for s in syntheses if _mean_similarity_score(s.similarity) >= threshold]


def _join_to_candidates(
    retrieved: list[Candidate], ranked: list[ScoredCandidate]
) -> list[Candidate]:
    """Re-attach :class:`Candidate` payloads to the rerank-ordered ids.

    The reranker returns :class:`ScoredCandidate` (id and perspective scores)
    but synthesize needs the full :class:`Candidate` (with ``payload.body``).
    We preserve the rerank order and silently drop any ranked id missing
    from the retrieved set. Defensive only, since the reranker only sees
    retrieved ids.
    """
    id_to_candidate = {c.canonical_id: c for c in retrieved}
    out: list[Candidate] = []
    for s in ranked:
        cand = id_to_candidate.get(s.candidate_id)
        if cand is not None:
            out.append(cand)
    return out


def _current_trace_id() -> str | None:
    """Return the current Laminar trace id, or ``None`` when tracing is off."""
    if not Laminar.is_initialized():
        return None
    tid = Laminar.get_trace_id()
    return str(tid) if tid is not None else None


@observe(
    name="query",
    ignore_inputs=["llm", "embedding_client", "corpus", "budget", "progress"],
)
async def run_query(  # noqa: PLR0913 - every dep is required wiring at the call site
    input_ctx: InputContext,
    *,
    llm: LLMClient,
    embedding_client: EmbeddingClient,
    corpus: Corpus,
    config: Config,
    budget: Budget,
    progress: QueryProgress | None = None,
    sparse_encoder: SparseEncoder | None = None,
) -> Report:
    """Run the full retrieve + rerank + synthesize pipeline against *input_ctx*.

    Args:
        input_ctx: The user's :class:`InputContext` (name, description, optional
            recency filter).
        llm: Async :class:`LLMClient` shared by every LLM-driven stage.
        embedding_client: Async dense embedder used inside ``retrieve``.
        corpus: Read-side :class:`Corpus` impl (Qdrant in production, fake in tests).
        config: :class:`Config`. ``K_retrieve``, ``N_synthesize``, model ids,
            ``strict_deaths``.
        budget: Shared per-run :class:`Budget` for cost bookkeeping. Stages
            book costs through their LLM/embedding clients; this function only
            reads ``spent_usd``/``remaining`` at the end.
        progress: Optional :class:`QueryProgress` sink for phase-level updates.
            CLI wires a Rich-backed impl; passing ``None`` (or omitting) is
            equivalent to :class:`NullQueryProgress`. Pipeline never writes to
            stderr itself.
        sparse_encoder: Optional override for the BM25 sparse encoder forwarded
            to ``retrieve``. ``None`` lazy-loads the production fastembed model
            on first call. The recording helper passes a wrapped encoder so it
            can persist sparse cassettes alongside dense + LLM cassettes.

    Returns:
        A :class:`Report` carrying the input echo, generated_at, the synthesized
        :class:`Synthesis` candidates (only successes; per-candidate exceptions
        are dropped silently), and a :class:`PipelineMeta` with cost, latency,
        trace id, and budget bookkeeping. ``budget_exceeded`` is ``True`` iff
        a :class:`BudgetExceededError` truncated the run.
    """
    t0 = time.monotonic()
    successes: list[Synthesis] = []
    top_risks = TopRisks()
    budget_exceeded = False

    if Laminar.is_initialized():
        Laminar.set_span_attributes(
            {
                "run.id": mint_run_id(),
                "run.kind": "query",
                "run.git_sha": git_sha() or "",
                "config.taxonomy_version": config.taxonomy_version,
                "config.K_retrieve": config.K_retrieve,
                "config.N_synthesize": config.N_synthesize,
                "config.min_similarity_score": config.min_similarity_score,
                "config.strict_deaths": config.strict_deaths,
                "config.model_facet": config.model_facet,
                "config.model_rerank": config.model_rerank,
                "config.model_synthesize": config.model_synthesize,
            }
        )

    progress = progress if progress is not None else NullQueryProgress()
    try:
        progress.start_phase(QueryPhase.FACET_EXTRACT, total=1)
        facets = await extract_facets(
            input_ctx.description,
            llm,
            model=config.model_facet,
            max_tokens=config.max_tokens_facet,
        )
        progress.advance_phase(QueryPhase.FACET_EXTRACT)
        progress.end_phase(QueryPhase.FACET_EXTRACT)

        progress.start_phase(QueryPhase.RETRIEVE, total=1)
        cutoff = cutoff_iso(input_ctx.years_filter)
        retrieved = await retrieve(
            description=input_ctx.description,
            facets=facets,
            corpus=corpus,
            embedding_client=embedding_client,
            cutoff_iso=cutoff,
            strict_deaths=config.strict_deaths,
            k_retrieve=config.K_retrieve,
            sparse_encoder=sparse_encoder,
        )
        progress.advance_phase(QueryPhase.RETRIEVE)
        progress.end_phase(QueryPhase.RETRIEVE)

        progress.start_phase(QueryPhase.RERANK, total=1)
        reranked = await llm_rerank(
            retrieved,
            input_ctx.description,
            facets,
            llm,
            config,
            model=config.model_rerank,
            max_tokens=config.max_tokens_rerank,
        )
        progress.advance_phase(QueryPhase.RERANK)
        progress.end_phase(QueryPhase.RERANK)

        survivors = _filter_by_min_similarity(reranked.ranked, config.min_similarity_score)
        top_n = _join_to_candidates(retrieved, survivors)[: config.N_synthesize]

        progress.start_phase(QueryPhase.SYNTHESIZE, total=len(top_n))

        def _on_candidate_done(exc: BaseException | None) -> None:
            if exc is not None:
                progress.error(QueryPhase.SYNTHESIZE, f"{type(exc).__name__}: {exc}")
            progress.advance_phase(QueryPhase.SYNTHESIZE)

        synth_results = await synthesize_all(
            top_n,
            input_ctx,
            llm,
            config,
            model=config.model_synthesize,
            max_tokens=config.max_tokens_synthesize,
            on_candidate_done=_on_candidate_done,
        )
        successes = [s for s in synth_results if isinstance(s, Synthesis)]
        successes = _filter_synth_by_min_similarity(successes, config.min_similarity_score)
        # Consolidate runs inside the try block so a successful run gets full
        # top-risks. A budget-exceeded run skips it and returns the default-empty
        # TopRisks initialized above; the truncated-run shape stays minimal.
        top_risks = await consolidate_risks(
            successes,
            pitch=input_ctx.description,
            llm=llm,
            config=config,
            model=config.model_consolidate,
            max_tokens=config.max_tokens_consolidate,
        )
        progress.end_phase(QueryPhase.SYNTHESIZE)
    except BudgetExceededError:
        budget_exceeded = True
        if Laminar.is_initialized():
            Laminar.event(name=str(SpanEvent.BUDGET_EXCEEDED))

    return Report(
        input=input_ctx,
        generated_at=datetime.now(UTC),
        candidates=successes,
        top_risks=top_risks,
        pipeline_meta=PipelineMeta(
            K_retrieve=config.K_retrieve,
            N_synthesize=config.N_synthesize,
            min_similarity_score=config.min_similarity_score,
            models={
                "facet": config.model_facet,
                "rerank": config.model_rerank,
                "synthesize": config.model_synthesize,
            },
            cost_usd_total=budget.spent_usd,
            latency_ms_total=int((time.monotonic() - t0) * 1000),
            trace_id=_current_trace_id(),
            budget_remaining_usd=budget.remaining,
            budget_exceeded=budget_exceeded,
        ),
    )
