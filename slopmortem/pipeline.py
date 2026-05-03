"""Pipeline orchestration. All side-effecting deps injected; CLI wires them up.

``BudgetExceededError`` truncates the run and returns a partial
`Report` with ``budget_exceeded=True``. Per-candidate synthesis
failures don't abort — ``synthesize_all`` returns them as exception entries
which we drop before populating ``Report.candidates``.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from dateutil.relativedelta import relativedelta
from lmnr import Laminar, observe

from slopmortem.budget import BudgetExceededError
from slopmortem.models import PipelineMeta, Report, Synthesis, TopRisks
from slopmortem.stages import (
    consolidate_risks,
    extract_facets,
    llm_rerank,
    retrieve,
    synthesize_all,
)
from slopmortem.tracing import SpanEvent, git_sha, mint_run_id

if TYPE_CHECKING:
    from slopmortem.budget import Budget
    from slopmortem.config import Config
    from slopmortem.corpus import Corpus
    from slopmortem.llm import EmbeddingClient, LLMClient
    from slopmortem.models import Candidate, InputContext, ScoredCandidate, SimilarityScores
    from slopmortem.stages import SparseEncoder


logger = logging.getLogger(__name__)


class QueryPhase(StrEnum):
    """Phase keys used by `QueryProgress`."""

    FACET_EXTRACT = "facet_extract"
    RETRIEVE = "retrieve"
    RERANK = "rerank"
    SYNTHESIZE = "synthesize"


@runtime_checkable
class QueryProgress(Protocol):
    """Phase-level progress hooks for ``slopmortem query``.

    The default `NullQueryProgress` keeps the orchestrator decoupled
    from any UI library; the CLI wires a Rich implementation.
    """

    def start_phase(self, phase: QueryPhase, total: int) -> None: ...
    def advance_phase(self, phase: QueryPhase, n: int = 1) -> None: ...
    def end_phase(self, phase: QueryPhase) -> None: ...
    def set_phase_status(self, phase: QueryPhase, status: str | None) -> None: ...
    def log(self, message: str) -> None: ...
    def error(self, phase: QueryPhase, message: str) -> None: ...


class NullQueryProgress:
    """No-op `QueryProgress` for when no display surface is attached."""

    def start_phase(self, phase: QueryPhase, total: int) -> None: ...
    def advance_phase(self, phase: QueryPhase, n: int = 1) -> None: ...
    def end_phase(self, phase: QueryPhase) -> None: ...
    def set_phase_status(self, phase: QueryPhase, status: str | None) -> None: ...
    def log(self, message: str) -> None: ...
    def error(self, phase: QueryPhase, message: str) -> None: ...


def cutoff_iso(years_filter: int | None) -> str | None:
    """Compute the ISO date cutoff for *years_filter*.

    Floor to ``date()`` keeps the cutoff stable across the query's hour;
    retrieve takes dates (``YYYY-MM-DD``), not timestamps.
    """
    if years_filter is None:
        return None
    return (datetime.now(UTC) - relativedelta(years=years_filter)).date().isoformat()


def _mean_similarity_score(scores: SimilarityScores) -> float:
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
    return [s for s in ranked if _mean_similarity_score(s.perspective_scores) >= threshold]


def _filter_synth_by_min_similarity(
    syntheses: list[Synthesis], threshold: float
) -> list[Synthesis]:
    # Synthesis sometimes re-scores a candidate lower than rerank did, so a row
    # that cleared the rerank-side filter can come back below the bar.
    return [s for s in syntheses if _mean_similarity_score(s.similarity) >= threshold]


def _log_min_similarity_drop(*, dropped: int, total: int, stage: str, threshold: float) -> None:
    if dropped <= 0:
        return
    logger.info(
        "min_similarity dropped %d/%d candidates %s (threshold=%.2f)",
        dropped,
        total,
        stage,
        threshold,
    )


def _select_top_n(
    *,
    retrieved: list[Candidate],
    ranked: list[ScoredCandidate],
    threshold: float,
    n_synthesize: int,
) -> tuple[list[Candidate], int]:
    # ``dropped_count`` conflates min-sim drops with retrieve under-fill (the
    # reranker returns up to n_synthesize rows, fewer when retrieve under-fills).
    survivors = _filter_by_min_similarity(ranked, threshold)
    _log_min_similarity_drop(
        dropped=len(ranked) - len(survivors),
        total=len(ranked),
        stage="post-rerank",
        threshold=threshold,
    )
    top_n = _join_to_candidates(retrieved, survivors)[:n_synthesize]
    return top_n, max(0, n_synthesize - len(top_n))


def _join_to_candidates(
    retrieved: list[Candidate], ranked: list[ScoredCandidate]
) -> list[Candidate]:
    # Drops any ranked id missing from retrieved — defensive, since the
    # reranker only ever sees retrieved ids.
    id_to_candidate = {c.canonical_id: c for c in retrieved}
    out: list[Candidate] = []
    for s in ranked:
        cand = id_to_candidate.get(s.candidate_id)
        if cand is not None:
            out.append(cand)
    return out


def _current_trace_id(*, enable_tracing: bool) -> str | None:
    # Gated on enable_tracing because @observe can still mint an OTel trace id
    # via TracerWrapper after Laminar.shutdown(), leaking a trace_id into runs
    # the user never asked to trace.
    if not enable_tracing or not Laminar.is_initialized():
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
    """Run the query pipeline end-to-end and assemble the `Report`.

    Per-candidate synthesis exceptions are dropped silently; ``BudgetExceededError``
    truncates the run and surfaces as ``pipeline_meta.budget_exceeded=True``.
    """
    t0 = time.monotonic()
    successes: list[Synthesis] = []
    top_risks = TopRisks()
    budget_exceeded = False
    filtered_pre_synth = 0
    filtered_post_synth = 0

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

        top_n, filtered_pre_synth = _select_top_n(
            retrieved=retrieved,
            ranked=reranked.ranked,
            threshold=config.min_similarity_score,
            n_synthesize=config.N_synthesize,
        )

        progress.start_phase(QueryPhase.SYNTHESIZE, total=len(top_n))
        # First synthesize call runs alone to warm Anthropic's prompt cache before
        # the fan-out (see synthesize_all). Surface it on the bar so users don't
        # read the 0/N as "stuck".
        progress.set_phase_status(QueryPhase.SYNTHESIZE, "warming prompt cache")
        warmup_cleared = False

        def _on_candidate_done(exc: BaseException | None) -> None:
            nonlocal warmup_cleared
            if not warmup_cleared:
                progress.set_phase_status(QueryPhase.SYNTHESIZE, None)
                warmup_cleared = True
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
        synth_in = len(successes)
        successes = _filter_synth_by_min_similarity(successes, config.min_similarity_score)
        filtered_post_synth = synth_in - len(successes)
        _log_min_similarity_drop(
            dropped=filtered_post_synth,
            total=synth_in,
            stage="post-synth",
            threshold=config.min_similarity_score,
        )
        # Inside the try so a budget-exceeded run falls through to the default
        # empty TopRisks instead of consolidating a partial set.
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
            trace_id=_current_trace_id(enable_tracing=config.enable_tracing),
            budget_remaining_usd=budget.remaining,
            budget_exceeded=budget_exceeded,
            filtered_pre_synth=filtered_pre_synth,
            filtered_post_synth=filtered_post_synth,
        ),
    )
