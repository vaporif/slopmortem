"""Per-entry fan-out: facet -> summarize -> embed -> upsert.

Slop classification gates entries upstream of fan-out (see _slop_gate.py).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import anyio

from slopmortem.concurrency import gather_resilient
from slopmortem.corpus import chunk_markdown
from slopmortem.ingest._ports import (
    IngestPhase,
    NullProgress,
    _Point,
)
from slopmortem.llm import prompt_template_sha, render_prompt

if TYPE_CHECKING:
    from collections.abc import Sequence

    from slopmortem.config import Config
    from slopmortem.ingest._ports import (
        Corpus,
        IngestProgress,
        SparseEncoder,
    )
    from slopmortem.llm import EmbeddingClient, LLMClient
    from slopmortem.models import CandidatePayload, Facets, RawEntry

__all__ = ["_FanoutResult", "_embed_and_upsert", "_facet_summarize_fanout"]


@dataclass
class _FanoutResult:
    facets: Facets
    summary: str
    cache_read: int
    cache_creation: int


async def _facet_call(
    text: str,
    *,
    llm: LLMClient,
    model: str | None,
    max_tokens: int | None = None,
) -> Facets:
    # ValidationError propagates to the per-entry isolator in ``ingest()`` so
    # one bad doc doesn't kill the run.
    from slopmortem.stages import extract_facets  # noqa: PLC0415

    return await extract_facets(text, llm, model, max_tokens=max_tokens)


async def _summarize_call(
    text: str,
    *,
    llm: LLMClient,
    model: str | None,
    max_tokens: int | None = None,
) -> tuple[str, int, int]:
    """Inlined; ``summarize_for_rerank`` discards the raw cache-token counters."""
    prompt = render_prompt("summarize", body=text, source_id="")
    res = await llm.complete(
        prompt,
        model=model,
        cache=True,
        extra_body={"prompt_template_sha": prompt_template_sha("summarize")},
        max_tokens=max_tokens,
    )
    summary = res.text.strip()
    return summary, res.cache_read_tokens or 0, res.cache_creation_tokens or 0


async def _facet_summarize_fanout(
    entries: Sequence[tuple[RawEntry, str]],
    *,
    llm: LLMClient,
    config: Config,
    progress: IngestProgress | None = None,
) -> list[_FanoutResult | Exception]:
    """Run facet+summarize across entries with bounded concurrency.

    Facet and summarize for the same entry run sequentially so two LLM calls
    never share one limiter slot. Returns one `_FanoutResult` per entry
    in order, or the exception that aborted it.
    """
    limiter = anyio.CapacityLimiter(config.ingest_concurrency)
    bar = progress or NullProgress()

    async def _run(text: str) -> _FanoutResult:
        async with limiter:
            facets = await _facet_call(
                text,
                llm=llm,
                model=config.model_facet,
                max_tokens=config.max_tokens_facet,
            )
        async with limiter:
            summary, cr, cc = await _summarize_call(
                text,
                llm=llm,
                model=config.model_summarize,
                max_tokens=config.max_tokens_summarize,
            )
        bar.advance_phase(IngestPhase.FAN_OUT)
        return _FanoutResult(facets=facets, summary=summary, cache_read=cr, cache_creation=cc)

    return await gather_resilient(*(_run(text) for _, text in entries))


async def _embed_and_upsert(  # noqa: PLR0913 - every dependency is required at the chunk site
    *,
    canonical_id: str,
    body: str,
    payload: CandidatePayload,
    corpus: Corpus,
    embed_client: EmbeddingClient,
    embed_model_id: str,
    sparse_encoder: SparseEncoder,
) -> int:
    chunks = chunk_markdown(body, parent_canonical_id=canonical_id)
    if not chunks:
        return 0
    texts = [c.text for c in chunks]
    embed_result = await embed_client.embed(texts, model=embed_model_id)
    base_payload = payload.model_dump(mode="json") | {"canonical_id": canonical_id}
    for c, vec in zip(chunks, embed_result.vectors, strict=True):
        sparse = sparse_encoder(c.text)
        point_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{canonical_id}:{c.chunk_idx}").hex
        point = _Point(
            id=point_id,
            vector={"dense": vec, "sparse": sparse},
            payload={**base_payload, "chunk_idx": c.chunk_idx},
        )
        await corpus.upsert_chunk(point)
    return len(chunks)
