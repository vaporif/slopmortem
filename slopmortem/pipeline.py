"""Top-level orchestration: ``run_query`` wires every stage of the pipeline.

Pure library code: no I/O, no stderr, no file access, no outbound HTTP. Every
side-effecting capability is injected (LLM client, embedding client, Corpus,
Budget, optional progress callback). The CLI in ``slopmortem.cli`` builds those
deps and calls ``run_query``.

Failure model:
- A :class:`BudgetExceededError` from any stage truncates the run. Whatever
  Synthesis values had accumulated up to that point come back in the final
  :class:`Report` with ``budget_exceeded=True``.
- Per-candidate synthesis failures do NOT abort the run. ``synthesize_all``
  swallows them as exception entries; we filter those out so ``Report.candidates``
  only carries successes.
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


logger = logging.getLogger(__name__)


class QueryPhase(StrEnum):
    """Phase keys used by :class:`QueryProgress`.

    Mirrors :class:`slopmortem.ingest.IngestPhase`. Closed enum so typos
    (``"facetextract"``) fail at parse time and match statements stay exhaustive.
    """

    FACET_EXTRACT = "facet_extract"
    RETRIEVE = "retrieve"
    RERANK = "rerank"
    SYNTHESIZE = "synthesize"


@runtime_checkable
class QueryProgress(Protocol):
    """Phase-level progress hooks for ``slopmortem query``.

    Methods are no-op-safe. :class:`NullQueryProgress` is the default so the
    orchestrator stays decoupled from any UI library; the CLI wires a Rich
    implementation. Mirrors :class:`slopmortem.ingest.IngestProgress`.
    """

    def start_phase(self, phase: QueryPhase, total: int) -> None:
        """Announce *phase* with an expected ``total`` of advances."""

    def advance_phase(self, phase: QueryPhase, n: int = 1) -> None:
        """Advance *phase*'s bar by ``n``."""

    def end_phase(self, phase: QueryPhase) -> None:
        """Mark *phase* complete."""

    def set_phase_status(self, phase: QueryPhase, status: str | None) -> None:
        """Set or clear a transient status suffix on *phase*'s display label."""

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

    def set_phase_status(self, phase: QueryPhase, status: str | None) -> None:
        """No-op."""

    def log(self, message: str) -> None:
        """No-op."""

    def error(self, phase: QueryPhase, message: str) -> None:
        """No-op."""


def cutoff_iso(years_filter: int | None) -> str | None:
    """Convert a years-back recency filter to an ISO-8601 date.

    Retrieve takes dates (``YYYY-MM-DD``), not timestamps. Flooring to
    ``date()`` here keeps the cutoff stable across the query's hour.
    """
    if years_filter is None:
        return None
    return (datetime.now(UTC) - relativedelta(years=years_filter)).date().isoformat()


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
    that cleared the rerank-side filter can come back below the bar. Second
    pass keeps the rendered table consistent.
    """
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
    """Apply min-similarity, join to candidates, cap at N. Returns (top_n, dropped_count).

    The reranker returns up to ``n_synthesize`` rows — fewer when retrieve
    under-fills. ``dropped_count`` conflates that under-fill with min-sim
    drops.
    """
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
    """Re-attach :class:`Candidate` payloads to the rerank-ordered ids.

    Reranker returns :class:`ScoredCandidate` (id + scores); synthesize needs
    the full :class:`Candidate` with ``payload.body``. Preserves rerank order
    and silently drops any ranked id missing from the retrieved set —
    defensive, since the reranker only ever sees retrieved ids.
    """
    id_to_candidate = {c.canonical_id: c for c in retrieved}
    out: list[Candidate] = []
    for s in ranked:
        cand = id_to_candidate.get(s.candidate_id)
        if cand is not None:
            out.append(cand)
    return out


def _current_trace_id(*, enable_tracing: bool) -> str | None:
    """Return the current Laminar trace id, or ``None`` when tracing is off.

    Gated on ``enable_tracing`` because ``Laminar.is_initialized()`` alone
    isn't sufficient: ``@observe`` can still mint an OTel trace id via
    ``TracerWrapper`` after ``Laminar.shutdown()``, leaking a trace_id into
    runs the user never asked to trace.
    """
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
    """Run the full retrieve + rerank + synthesize pipeline against *input_ctx*.

    Args:
        input_ctx: User's :class:`InputContext` (name, description, optional
            recency filter).
        llm: Async :class:`LLMClient` shared by every LLM stage.
        embedding_client: Async dense embedder used inside ``retrieve``.
        corpus: Read-side :class:`Corpus` impl (Qdrant in prod, fake in tests).
        config: :class:`Config` — ``K_retrieve``, ``N_synthesize``, model ids,
            ``strict_deaths``.
        budget: Shared per-run :class:`Budget`. Stages book costs through their
            clients; this function only reads ``spent_usd`` / ``remaining`` at
            the end.
        progress: Optional :class:`QueryProgress` sink. CLI wires a Rich impl;
            ``None`` falls back to :class:`NullQueryProgress`. Pipeline never
            writes stderr itself.
        sparse_encoder: Optional BM25 encoder override forwarded to ``retrieve``.
            ``None`` lazy-loads the production fastembed model on first call.
            The recording helper passes a wrapped encoder so it can persist
            sparse cassettes alongside dense + LLM cassettes.

    Returns:
        A :class:`Report` with the input echo, generated_at, the synthesized
        :class:`Synthesis` values (successes only; per-candidate exceptions are
        dropped silently), and a :class:`PipelineMeta` carrying cost, latency,
        trace id, and budget bookkeeping. ``budget_exceeded`` is ``True`` iff a
        :class:`BudgetExceededError` truncated the run.
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
