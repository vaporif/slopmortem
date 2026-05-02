# pyright: reportAny=false
"""Ingest orchestration entry point.

This module holds only the `ingest()` function. Types, protocols, dataclasses,
and pure helpers live in `_orchestrator.py`; per-stage logic lives in
`_warm_cache.py`, `_fan_out.py`, `_journal_writes.py`, and `_slop_gate.py`.
The split keeps the package's dependency graph acyclic.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lmnr import Laminar, observe

from slopmortem.ingest._fan_out import (
    _facet_summarize_fanout,
    _FanoutResult,
)
from slopmortem.ingest._journal_writes import (
    ProcessOutcome,
    _process_entry,
)
from slopmortem.ingest._orchestrator import (
    _MAX_RECORDED_ERRORS,  # pyright: ignore[reportPrivateUsage]  -- intra-package private boundary
    IngestPhase,
    IngestResult,
    NullProgress,
    _enrich_pipeline,  # pyright: ignore[reportPrivateUsage]  -- intra-package private boundary
    _entry_summary_text,  # pyright: ignore[reportPrivateUsage]  -- intra-package private boundary
    _gather_entries,  # pyright: ignore[reportPrivateUsage]  -- intra-package private boundary
)
from slopmortem.ingest._slop_gate import (
    _quarantine,
    classify_one,
)
from slopmortem.ingest._warm_cache import cache_read_ratio_event, cache_warm
from slopmortem.tracing import SpanEvent, git_sha, mint_run_id

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from slopmortem.budget import Budget
    from slopmortem.config import Config
    from slopmortem.corpus import MergeJournal
    from slopmortem.corpus.sources import Enricher, Source
    from slopmortem.ingest._orchestrator import (
        Corpus,
        IngestProgress,
        SlopClassifier,
        SparseEncoder,
    )
    from slopmortem.llm import EmbeddingClient, LLMClient
    from slopmortem.models import RawEntry

__all__ = ["ingest"]

logger = logging.getLogger(__name__)


@observe(
    name="ingest",
    ignore_inputs=[
        "sources",
        "enrichers",
        "journal",
        "corpus",
        "llm",
        "embed_client",
        "budget",
        "slop_classifier",
        "sparse_encoder",
        "progress",
    ],
)
async def ingest(  # noqa: PLR0913, C901, PLR0912, PLR0915 - orchestration takes every dependency.
    *,
    sources: Sequence[Source],
    enrichers: Sequence[Enricher],
    journal: MergeJournal,
    corpus: Corpus,
    llm: LLMClient,
    embed_client: EmbeddingClient,
    budget: Budget,  # noqa: ARG001 - consumed by LLM/embed clients at construction time
    slop_classifier: SlopClassifier,
    config: Config,
    post_mortems_root: Path,
    dry_run: bool = False,
    force: bool = False,
    sparse_encoder: SparseEncoder | None = None,
    limit: int | None = None,
    progress: IngestProgress | None = None,
) -> IngestResult:
    """Run one full ingest pass and return the aggregated :class:`IngestResult`.

    Per-entry and per-source failures log and continue; only budget exhaustion
    truncates the run. ``dry_run`` counts entries without writing journal,
    disk, or qdrant. ``force`` bypasses the skip_key short-circuit.
    ``sparse_encoder=None`` lazy-loads the production fastembed model; tests
    pass a no-op stub to dodge the ~150 MB ONNX download.
    """
    result = IngestResult(dry_run=dry_run)
    progress = progress or NullProgress()

    # Closure so every early-return path can drain the events; the list itself
    # stays in result for tests and the CLI renderer.
    def _emit_collected_events() -> None:
        if not Laminar.is_initialized():
            return
        for name in result.span_events:
            Laminar.event(name=name)

    # Without per-entry attributes, swallowed exceptions only show up in stderr
    # — the parent span returns OK and INGEST_ENTRY_FAILED carries no payload.
    def _record_error(entry_label: str, exc: BaseException) -> None:
        if not Laminar.is_initialized():
            return
        idx = result.errors
        if idx >= _MAX_RECORDED_ERRORS:
            Laminar.set_span_attributes({"errors.truncated_count": idx - _MAX_RECORDED_ERRORS + 1})
            return
        Laminar.set_span_attributes(
            {
                f"errors.{idx}.entry": entry_label,
                f"errors.{idx}.exception_type": type(exc).__name__,
                f"errors.{idx}.message": str(exc)[:500],
            }
        )

    if Laminar.is_initialized():
        Laminar.set_span_attributes(
            {
                "run.id": mint_run_id(),
                "run.kind": "ingest",
                "run.git_sha": git_sha() or "",
                "run.dry_run": dry_run,
                "run.force": force,
                "run.limit": limit if limit is not None else 0,
                "config.taxonomy_version": config.taxonomy_version,
                "config.reliability_rank_version": config.reliability_rank_version,
                "config.slop_threshold": config.slop_threshold,
                "config.model_facet": config.model_facet,
                "config.model_summarize": config.model_summarize,
            }
        )

    # Default sparse encoder: BM25 via fastembed. Tests stub it with a
    # dict-returning lambda so the ONNX model never loads under pytest.
    if sparse_encoder is None:
        from slopmortem.corpus._embed_sparse import encode as _encode_sparse  # noqa: PLC0415

        sparse_encoder = _encode_sparse

    # When ``--limit`` is set, ``total=limit`` gives Rich a real denominator
    # so the ETA column works. Without it, the count isn't known up front,
    # so pass ``None`` (indeterminate, pulsing bar) rather than lying with 0.
    progress.start_phase(IngestPhase.GATHER, total=limit)
    entries, source_failures = await _gather_entries(
        sources, span_events=result.span_events, limit=limit, progress=progress
    )
    progress.end_phase(IngestPhase.GATHER)
    progress.log(f"gathered {len(entries)} entries from {len(sources)} sources")
    result.source_failures = source_failures

    progress.start_phase(IngestPhase.CLASSIFY, total=len(entries))
    keepers: list[tuple[RawEntry, str]] = []  # (entry, body) post-extract.
    for entry in entries:
        result.seen += 1
        try:
            enriched = await _enrich_pipeline(entry, enrichers)
        except Exception as exc:  # noqa: BLE001 - per-entry isolation.
            logger.warning("ingest: enricher failed for %r: %s", entry.source_id, exc)
            progress.error(IngestPhase.CLASSIFY, f"enricher failed for {entry.source_id}: {exc}")
            _record_error(f"{entry.source}:{entry.source_id}", exc)
            result.errors += 1
            result.span_events.append(SpanEvent.INGEST_ENTRY_FAILED.value)
            progress.advance_phase(IngestPhase.CLASSIFY)
            continue

        body = _entry_summary_text(enriched, max_tokens=config.max_doc_tokens)
        if not body:
            result.skipped += 1
            progress.advance_phase(IngestPhase.CLASSIFY)
            continue

        slop_score = await classify_one(
            entry=enriched,
            body=body,
            slop_classifier=slop_classifier,
            on_error=lambda exc: progress.error(
                IngestPhase.CLASSIFY, f"slop classifier failed: {exc}"
            ),
        )

        if slop_score > config.slop_threshold:
            if not dry_run:
                await _quarantine(
                    journal=journal,
                    entry=enriched,
                    body=body,
                    slop_score=slop_score,
                    post_mortems_root=post_mortems_root,
                )
            result.quarantined += 1
            result.span_events.append(SpanEvent.SLOP_QUARANTINED.value)
            progress.advance_phase(IngestPhase.CLASSIFY)
            continue

        keepers.append((enriched, body))
        progress.advance_phase(IngestPhase.CLASSIFY)
    progress.end_phase(IngestPhase.CLASSIFY)
    quarantined = result.quarantined
    skipped = result.skipped
    progress.log(f"classified: {len(keepers)} kept, {quarantined} quarantined, {skipped} skipped")

    if dry_run:
        result.would_process = len(keepers)
        _emit_collected_events()
        return result

    if not keepers:
        _emit_collected_events()
        return result

    progress.start_phase(IngestPhase.CACHE_WARM, total=1)
    warmed, warm_creation, warm_events = await cache_warm(
        llm=llm,
        model=config.model_summarize,
        seed_text=keepers[0][1][:1000],
        max_tokens=config.max_tokens_summarize,
    )
    progress.advance_phase(IngestPhase.CACHE_WARM)
    progress.end_phase(IngestPhase.CACHE_WARM)
    result.cache_warmed = warmed
    result.cache_creation_tokens_warm = warm_creation
    result.span_events.extend(warm_events)

    progress.start_phase(IngestPhase.FAN_OUT, total=len(keepers))
    fanout = await _facet_summarize_fanout(keepers, llm=llm, config=config, progress=progress)
    progress.end_phase(IngestPhase.FAN_OUT)

    ratio_event = cache_read_ratio_event([r for r in fanout if isinstance(r, _FanoutResult)])
    if ratio_event:
        result.span_events.append(ratio_event)

    progress.start_phase(IngestPhase.WRITE, total=len(keepers))
    for (entry, body), fan in zip(keepers, fanout, strict=True):
        if isinstance(fan, BaseException):
            logger.warning(
                "ingest: fan-out failed for %s:%s: %s", entry.source, entry.source_id, fan
            )
            progress.error(
                IngestPhase.FAN_OUT,
                f"fan-out failed for {entry.source}:{entry.source_id}: {fan}",
            )
            _record_error(f"{entry.source}:{entry.source_id}", fan)
            result.errors += 1
            result.span_events.append(SpanEvent.INGEST_ENTRY_FAILED.value)
            progress.advance_phase(IngestPhase.WRITE)
            continue
        try:
            outcome = await _process_entry(
                entry,
                body=body,
                fan=fan,
                journal=journal,
                corpus=corpus,
                embed_client=embed_client,
                llm=llm,
                config=config,
                post_mortems_root=post_mortems_root,
                slop_score=0.0,  # we already filtered slop above
                force=force,
                span_events=result.span_events,
                sparse_encoder=sparse_encoder,
            )
        except Exception as exc:  # noqa: BLE001 - per-entry isolation; run continues.
            logger.warning(
                "ingest: write phase failed for %s:%s: %s",
                entry.source,
                entry.source_id,
                exc,
            )
            progress.error(
                IngestPhase.WRITE,
                f"write phase failed for {entry.source}:{entry.source_id}: {exc}",
            )
            _record_error(f"{entry.source}:{entry.source_id}", exc)
            result.errors += 1
            result.span_events.append(SpanEvent.INGEST_ENTRY_FAILED.value)
            progress.advance_phase(IngestPhase.WRITE)
            continue
        except BaseException as exc:
            # CancelledError / SystemExit / etc; ``except Exception`` above misses
            # these. Surface what's escaping (which entry, what type) via the
            # progress error channel before letting it propagate, so the run
            # terminates loud rather than silent.
            progress.error(
                IngestPhase.WRITE,
                f"FATAL {type(exc).__name__} on {entry.source}:{entry.source_id}: {exc}",
            )
            raise
        match outcome:
            case ProcessOutcome.PROCESSED:
                result.processed += 1
            case ProcessOutcome.SKIPPED:
                result.skipped += 1
            case ProcessOutcome.SKIPPED_EMPTY:
                result.skipped_empty += 1
            case ProcessOutcome.FAILED:
                result.failed += 1
        progress.advance_phase(IngestPhase.WRITE)
    progress.end_phase(IngestPhase.WRITE)

    _emit_collected_events()
    return result
