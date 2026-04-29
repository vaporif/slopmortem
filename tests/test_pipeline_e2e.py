"""End-to-end pipeline tests: full ``run_query`` path with fakes for every dependency.

Covers Task 10 plan steps:
- 10.1 Full pipeline E2E with fake LLM/embedder/Corpus.
- 10.5 Ctrl-C cancel propagates as ``asyncio.CancelledError``.

Tests use ``FakeLLMClient`` (canned ``(prompt_template_sha, model)`` responses)
and ``FakeEmbeddingClient`` (sha256-derived vectors). The Corpus is an
in-memory implementation of :class:`slopmortem.corpus.store.Corpus`; no
Qdrant required.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

import pytest

from slopmortem.budget import Budget
from slopmortem.config import Config
from slopmortem.llm.fake import FakeLLMClient, FakeResponse
from slopmortem.llm.fake_embeddings import FakeEmbeddingClient
from slopmortem.llm.prompts import prompt_template_sha
from slopmortem.models import Candidate, CandidatePayload, Facets, InputContext, Synthesis
from slopmortem.pipeline import _cutoff_iso, _join_to_candidates, run_query

if TYPE_CHECKING:
    from collections.abc import Mapping

    from slopmortem.llm.client import CompletionResult

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

_FACET_MODEL = "test-facet"
_RERANK_MODEL = "test-rerank"
_SYNTH_MODEL = "test-synth"
_EMBED_MODEL = "text-embedding-3-small"


def _facets() -> Facets:
    return Facets(
        sector="fintech",
        business_model="b2b_saas",
        customer_type="smb",
        geography="us",
        monetization="subscription_recurring",
    )


def _payload(*, name: str, canonical_id: str) -> CandidatePayload:
    return CandidatePayload(
        name=name,
        summary=f"{name} was a B2B fintech.",
        body=f"{name} was a B2B fintech that ran out of runway.",
        facets=_facets(),
        founding_date=date(2018, 1, 1),
        failure_date=date(2023, 1, 1),
        founding_date_unknown=False,
        failure_date_unknown=False,
        provenance="curated_real",
        slop_score=0.0,
        sources=["https://news.ycombinator.com/item?id=" + canonical_id],
        text_id=canonical_id.replace("-", "") + "0123456789",
    )


def _candidate(canonical_id: str, *, score: float = 0.9) -> Candidate:
    return Candidate(
        canonical_id=canonical_id,
        score=score,
        payload=_payload(name=canonical_id, canonical_id=canonical_id),
    )


def _facet_extract_payload() -> str:
    return json.dumps(
        {
            "sector": "fintech",
            "business_model": "b2b_saas",
            "customer_type": "smb",
            "geography": "us",
            "monetization": "subscription_recurring",
            "sub_sector": "smb invoicing",
            "product_type": "saas",
            "price_point": "tiered",
            "founding_year": 2024,
            "failure_year": None,
        }
    )


def _rerank_payload(canonical_ids: list[str]) -> str:
    ranked = [
        {
            "candidate_id": cid,
            "perspective_scores": {
                "business_model": {"score": 7.0, "rationale": "match"},
                "market": {"score": 6.0, "rationale": "match"},
                "gtm": {"score": 5.0, "rationale": "match"},
                "stage_scale": {"score": 4.0, "rationale": "match"},
            },
            "rationale": "ranked",
        }
        for cid in canonical_ids
    ]
    return json.dumps({"ranked": ranked})


def _synthesis_payload(canonical_id: str) -> str:
    return json.dumps(
        {
            "candidate_id": canonical_id,
            "name": canonical_id,
            "one_liner": "B2B fintech for SMB invoicing.",
            "failure_date": "2023-01-01",
            "lifespan_months": 60,
            "similarity": {
                "business_model": {"score": 7.0, "rationale": "both B2B SaaS"},
                "market": {"score": 6.0, "rationale": "SMB overlap"},
                "gtm": {"score": 5.0, "rationale": "outbound sales"},
                "stage_scale": {"score": 4.0, "rationale": "seed stage"},
            },
            "why_similar": "Both target SMB invoicing.",
            "where_diverged": "Pitch is web-first; analogue was mobile-only.",
            "failure_causes": ["CAC > LTV"],
            "lessons_for_input": ["target larger ACVs"],
            "sources": [f"https://news.ycombinator.com/item?id={canonical_id}"],
        }
    )


def _build_canned(
    *, candidate_ids: list[str]
) -> Mapping[tuple[str, str], FakeResponse | CompletionResult]:
    """Build the FakeLLMClient canned-response map for the full pipeline."""
    canned: dict[tuple[str, str], FakeResponse | CompletionResult] = {
        (prompt_template_sha("facet_extract"), _FACET_MODEL): FakeResponse(
            text=_facet_extract_payload(), cost_usd=0.001
        ),
        (prompt_template_sha("llm_rerank"), _RERANK_MODEL): FakeResponse(
            text=_rerank_payload(candidate_ids), cost_usd=0.005
        ),
        # Synthesis returns the same canned response for every candidate. The
        # ``candidate_id`` field stays "acme" but the stage's parser doesn't
        # enforce that it matches the request.
        (prompt_template_sha("synthesize"), _SYNTH_MODEL): FakeResponse(
            text=_synthesis_payload("acme"), cost_usd=0.01, cache_creation_tokens=10
        ),
    }
    return canned


@dataclass
class _FakeCorpus:
    """In-memory :class:`Corpus` for pipeline tests; no Qdrant, no fastembed."""

    candidates: list[Candidate]
    queries: list[dict[str, object]] = field(default_factory=list)

    async def query(  # noqa: PLR0913 — Protocol contract dictates the signature
        self,
        *,
        dense: list[float],
        sparse: dict[int, float],
        facets: Facets,
        cutoff_iso: str | None,
        strict_deaths: bool,
        k_retrieve: int,
    ) -> list[Candidate]:
        self.queries.append(
            {
                "dense_dim": len(dense),
                "sparse_keys": list(sparse.keys()),
                "facets": facets.model_dump(),
                "cutoff_iso": cutoff_iso,
                "strict_deaths": strict_deaths,
                "k_retrieve": k_retrieve,
            }
        )
        return list(self.candidates[:k_retrieve])

    async def get_post_mortem(self, canonical_id: str) -> str:
        for c in self.candidates:
            if c.canonical_id == canonical_id:
                return c.payload.body
        msg = f"unknown canonical_id {canonical_id!r}"
        raise KeyError(msg)

    async def search_corpus(
        self, q: str, facets: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:
        del q, facets
        return [
            {
                "canonical_id": c.canonical_id,
                "name": c.payload.name,
                "summary": c.payload.summary,
                "score": c.score,
            }
            for c in self.candidates
        ]


def _no_op_sparse_encoder(_t: str) -> dict[int, float]:
    return {1: 1.0}


def _build_config(*, k_retrieve: int = 6, n_synthesize: int = 3) -> Config:
    cfg = Config()
    return cfg.model_copy(
        update={
            "K_retrieve": k_retrieve,
            "N_synthesize": n_synthesize,
            "model_facet": _FACET_MODEL,
            "model_rerank": _RERANK_MODEL,
            "model_synthesize": _SYNTH_MODEL,
        }
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_full_pipeline_with_fake_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the full pipeline end-to-end with fakes; assert the Report shape."""
    candidates = [_candidate(f"cand-{i}") for i in range(6)]
    cfg = _build_config(k_retrieve=6, n_synthesize=3)
    canned = _build_canned(candidate_ids=[c.canonical_id for c in candidates[:3]])
    fake_llm = FakeLLMClient(canned=canned, default_model=_SYNTH_MODEL)
    fake_embed = FakeEmbeddingClient(model=_EMBED_MODEL)
    fake_corpus = _FakeCorpus(candidates=candidates)
    budget = Budget(cap_usd=2.0)

    # Override retrieve's default sparse encoder; avoids loading fastembed.
    monkeypatch.setattr("slopmortem.corpus.embed_sparse.encode", _no_op_sparse_encoder)

    ctx = InputContext(name="newco", description="A B2B fintech for SMB invoicing")

    progress_events: list[str] = []
    report = await run_query(
        ctx,
        llm=fake_llm,
        embedding_client=fake_embed,
        corpus=fake_corpus,
        config=cfg,
        budget=budget,
        progress=progress_events.append,
    )

    # Report shape
    assert report.input == ctx
    assert isinstance(report.candidates, list)
    assert 0 < len(report.candidates) <= cfg.N_synthesize
    assert all(isinstance(s, Synthesis) for s in report.candidates)

    # Pipeline meta
    meta = report.pipeline_meta
    assert meta.K_retrieve == cfg.K_retrieve
    assert meta.N_synthesize == cfg.N_synthesize
    # FakeLLMClient/FakeEmbeddingClient don't push costs into the budget. We
    # still assert ``cost_usd_total`` reads from ``Budget.spent_usd`` so the
    # pipeline doesn't hand-roll the figure.
    assert meta.cost_usd_total == budget.spent_usd
    assert meta.latency_ms_total >= 0
    assert meta.budget_exceeded is False
    assert meta.trace_id is None  # tracing not initialized in tests
    assert set(meta.models.keys()) == {"facet", "rerank", "synthesize"}
    assert meta.models["facet"] == _FACET_MODEL
    assert meta.models["rerank"] == _RERANK_MODEL
    assert meta.models["synthesize"] == _SYNTH_MODEL

    # Progress callback was invoked at every stage
    assert "facet_extract" in progress_events
    assert "retrieve" in progress_events
    assert "rerank" in progress_events
    assert any(p.startswith("synthesize") for p in progress_events)

    # Corpus.query was invoked with the right knobs.
    assert len(fake_corpus.queries) == 1
    q = fake_corpus.queries[0]
    assert q["k_retrieve"] == cfg.K_retrieve
    assert q["strict_deaths"] == cfg.strict_deaths


async def test_run_query_records_budget_exceeded(monkeypatch: pytest.MonkeyPatch) -> None:
    """BudgetExceededError mid-run sets ``budget_exceeded=True`` and returns cleanly."""
    candidates = [_candidate(f"cand-{i}") for i in range(6)]
    cfg = _build_config(k_retrieve=6, n_synthesize=3)
    canned = _build_canned(candidate_ids=[c.canonical_id for c in candidates[:3]])
    fake_llm = FakeLLMClient(canned=canned, default_model=_SYNTH_MODEL)
    fake_embed = FakeEmbeddingClient(model=_EMBED_MODEL)
    fake_corpus = _FakeCorpus(candidates=candidates)
    # Cap at 0.0 so any LLM call's cost reservation exceeds the budget.
    budget = Budget(cap_usd=0.0)

    monkeypatch.setattr("slopmortem.corpus.embed_sparse.encode", _no_op_sparse_encoder)

    # Force extract_facets to raise BudgetExceededError immediately. This
    # exercises the except-branch in ``run_query`` without needing the
    # embedding client to do real reservation accounting.
    from slopmortem.budget import BudgetExceededError  # noqa: PLC0415

    async def _raise(*_a: object, **_kw: object) -> None:
        msg = "test"
        raise BudgetExceededError(msg)

    monkeypatch.setattr("slopmortem.pipeline.extract_facets", _raise)

    ctx = InputContext(name="newco", description="A B2B fintech for SMB invoicing")
    report = await run_query(
        ctx,
        llm=fake_llm,
        embedding_client=fake_embed,
        corpus=fake_corpus,
        config=cfg,
        budget=budget,
    )

    assert report.pipeline_meta.budget_exceeded is True
    assert report.candidates == []


async def test_ctrl_c_cancels_in_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cancelling the run_query task propagates as ``asyncio.CancelledError``."""
    candidates = [_candidate(f"cand-{i}") for i in range(6)]
    cfg = _build_config(k_retrieve=6, n_synthesize=3)
    canned = _build_canned(candidate_ids=[c.canonical_id for c in candidates[:3]])

    @dataclass
    class _SlowFakeLLMClient:
        """FakeLLMClient that sleeps before each completion so cancel can land."""

        inner: FakeLLMClient

        async def complete(  # noqa: PLR0913 — mirrors LLMClient.complete signature
            self,
            prompt: str,
            *,
            system: str | None = None,
            tools: list[Any] | None = None,
            model: str | None = None,
            cache: bool = False,
            response_format: dict[str, Any] | None = None,
            extra_body: dict[str, Any] | None = None,
        ) -> CompletionResult:
            await asyncio.sleep(0.5)
            return await self.inner.complete(
                prompt,
                system=system,
                tools=tools,
                model=model,
                cache=cache,
                response_format=response_format,
                extra_body=extra_body,
            )

    slow_llm = _SlowFakeLLMClient(inner=FakeLLMClient(canned=canned, default_model=_SYNTH_MODEL))
    fake_embed = FakeEmbeddingClient(model=_EMBED_MODEL)
    fake_corpus = _FakeCorpus(candidates=candidates)
    budget = Budget(cap_usd=2.0)

    monkeypatch.setattr("slopmortem.corpus.embed_sparse.encode", _no_op_sparse_encoder)

    ctx = InputContext(name="newco", description="A B2B fintech for SMB invoicing")
    task = asyncio.create_task(
        run_query(
            ctx,
            llm=slow_llm,
            embedding_client=fake_embed,
            corpus=fake_corpus,
            config=cfg,
            budget=budget,
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# Helpers under test
# ---------------------------------------------------------------------------


def test_cutoff_iso_none_returns_none() -> None:
    assert _cutoff_iso(None) is None


def test_cutoff_iso_returns_iso_date_string() -> None:
    out = _cutoff_iso(years_filter=5)
    assert out is not None
    # Must be parseable as a date.
    date.fromisoformat(out)


def test_join_to_candidates_preserves_rerank_order() -> None:
    from slopmortem.models import (  # noqa: PLC0415
        PerspectiveScore,
        ScoredCandidate,
        SimilarityScores,
    )

    retrieved = [_candidate(f"cand-{i}") for i in range(5)]

    def _scored(cid: str) -> ScoredCandidate:
        return ScoredCandidate(
            candidate_id=cid,
            perspective_scores=SimilarityScores(
                business_model=PerspectiveScore(score=1.0, rationale="x"),
                market=PerspectiveScore(score=1.0, rationale="x"),
                gtm=PerspectiveScore(score=1.0, rationale="x"),
                stage_scale=PerspectiveScore(score=1.0, rationale="x"),
            ),
            rationale="r",
        )

    # Rerank flips the order.
    ranked = [_scored("cand-3"), _scored("cand-0"), _scored("cand-2")]
    joined = _join_to_candidates(retrieved, ranked)
    assert [c.canonical_id for c in joined] == ["cand-3", "cand-0", "cand-2"]


def test_join_to_candidates_drops_unknown_ids() -> None:
    from slopmortem.models import (  # noqa: PLC0415
        PerspectiveScore,
        ScoredCandidate,
        SimilarityScores,
    )

    retrieved = [_candidate("cand-0"), _candidate("cand-1")]
    ranked = [
        ScoredCandidate(
            candidate_id="ghost",
            perspective_scores=SimilarityScores(
                business_model=PerspectiveScore(score=1.0, rationale="x"),
                market=PerspectiveScore(score=1.0, rationale="x"),
                gtm=PerspectiveScore(score=1.0, rationale="x"),
                stage_scale=PerspectiveScore(score=1.0, rationale="x"),
            ),
            rationale="r",
        ),
    ]
    assert _join_to_candidates(retrieved, ranked) == []
