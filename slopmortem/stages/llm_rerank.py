"""LLM rerank stage: one strict-mode JSON call → `LlmRerankResult`."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lmnr import Laminar, observe

from slopmortem.errors import RerankLengthError
from slopmortem.llm import prompt_template_sha, render_prompt, to_strict_response_schema
from slopmortem.models import LlmRerankResult

if TYPE_CHECKING:
    from slopmortem.config import Config
    from slopmortem.llm import LLMClient
    from slopmortem.models import Candidate, Facets, ScoredCandidate


logger = logging.getLogger(__name__)


# Drop ``candidates`` from span attrs to keep ``payload.body`` out; a
# redacted ``(canonical_id, name)`` projection is re-attached below.
@observe(name="stage.llm_rerank", ignore_inputs=["candidates"])
async def llm_rerank(  # noqa: PLR0913 — every dependency is required at the call site
    candidates: list[Candidate],
    pitch: str,
    facets: Facets,
    llm: LLMClient,
    config: Config,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
) -> LlmRerankResult:
    """LLM-rerank *candidates* against *facets*.

    Strict-mode schema constrains shape but not length, so the parsed array
    is re-validated against ``min(N_synthesize, len(candidates))`` — mismatch
    raises `RerankLengthError`.
    """
    Laminar.set_span_attributes(
        {
            "candidates_meta": [
                {"canonical_id": c.canonical_id, "name": c.payload.name} for c in candidates
            ],
        }
    )
    # TODO(scaling): rerank cost grows linearly with K_retrieve (#27).
    # At K=30 the rubric prompt cache absorbs most of the latency. Bump K and
    # the candidate-list segment dominates. Pick later between (a) two-stage
    # rerank (cheap 30→10, expensive 10→5), (b) local cross-encoder (e.g.
    # bge-reranker) replacing the LLM call, or (c) tighter summary truncation.
    # Decide when measurements justify it.
    prompt = render_prompt(
        "llm_rerank",
        pitch=pitch,
        facets=facets.model_dump(),
        top_n=config.N_synthesize,
        candidates=[
            {
                "candidate_id": c.canonical_id,
                "name": c.payload.name,
                "summary": c.payload.summary,
            }
            for c in candidates
        ],
    )
    result = await llm.complete(
        prompt,
        model=model,
        cache=True,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "LlmRerankResult",
                "schema": to_strict_response_schema(LlmRerankResult),
                "strict": True,
            },
        },
        extra_body={"prompt_template_sha": prompt_template_sha("llm_rerank")},
        max_tokens=max_tokens,
    )
    parsed = LlmRerankResult.model_validate_json(result.text)
    expected = min(config.N_synthesize, len(candidates))
    if len(parsed.ranked) != expected:
        msg = f"expected {expected}, got {len(parsed.ranked)}"
        raise RerankLengthError(msg)
    return parsed


def _join_by_id(retrieved: list[Candidate], ranked: list[ScoredCandidate]) -> list[Candidate]:
    # Drops any ranked id missing from retrieved — defensive, since the
    # reranker only ever sees retrieved ids.
    id_to_candidate = {c.canonical_id: c for c in retrieved}
    out: list[Candidate] = []
    for s in ranked:
        cand = id_to_candidate.get(s.candidate_id)
        if cand is not None:
            out.append(cand)
    return out


def select_top_n_by_similarity(
    *,
    retrieved: list[Candidate],
    ranked: list[ScoredCandidate],
    min_similarity: float,
    n_synthesize: int,
) -> tuple[list[Candidate], int]:
    """Apply the post-rerank min-similarity filter and slice to *n_synthesize*.

    Returns ``(top_n, dropped_count)`` where ``dropped_count`` is
    ``n_synthesize - len(top_n)`` — conflates min-sim drops with retrieve
    under-fill, which is what ``PipelineMeta.filtered_pre_synth`` records.
    """
    survivors = [s for s in ranked if s.perspective_scores.mean() >= min_similarity]
    sim_dropped = len(ranked) - len(survivors)
    if sim_dropped > 0:
        logger.info(
            "min_similarity dropped %d/%d candidates post-rerank (threshold=%.2f)",
            sim_dropped,
            len(ranked),
            min_similarity,
        )
    top_n = _join_by_id(retrieved, survivors)[:n_synthesize]
    return top_n, max(0, n_synthesize - len(top_n))
