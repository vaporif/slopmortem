"""Record cassettes for a set of inputs end to end.

Handles the ephemeral Qdrant lifecycle, the recording wrappers, the two-step
atomic dir swap, and forces Tavily off. Test authors call this when they want
a per-test cassette set without re-implementing the plumbing.
"""

from __future__ import annotations

import os
import shutil
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from anyio import to_thread

from slopmortem.evals.qdrant_setup import setup_ephemeral_qdrant
from slopmortem.evals.recording import (
    RecordingEmbeddingClient,
    RecordingLLMClient,
    RecordingSparseEncoder,
)
from slopmortem.evals.recording_progress import (
    NullRecordProgress,
    RecordPhase,
    RecordProgress,
)
from slopmortem.pipeline import QueryPhase, run_query

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from slopmortem.config import Config
    from slopmortem.llm.client import CompletionResult
    from slopmortem.models import InputContext


_DEFAULT_MAX_COST_USD = 2.0
_STALE_TMP_SECONDS = 24 * 3600


@dataclass(frozen=True, slots=True)
class RecordResult:
    """Aggregate counters for one ``record_cassettes_for_inputs`` invocation.

    Internal result type the runner consumes for the post-run footer. Counts
    are computed during the run (rows attempted/succeeded, cassettes written
    to disk after each successful atomic swap) and the running USD spend is
    forwarded from the per-call ``on_cost`` hook.
    """

    rows_total: int
    rows_succeeded: int
    cassettes_written: int
    total_cost_usd: float


def _sweep_stale_recording_dirs(root: Path, *, max_age_seconds: int) -> None:
    """Remove ``*.recording`` dirs older than ``max_age_seconds`` under ``root``.

    Best-effort. Any stat/rm failure is swallowed. Silently no-op when
    ``root`` doesn't exist yet.
    """
    if not root.exists():
        return
    cutoff = time.time() - max_age_seconds
    for d in root.glob("**/*.recording"):
        try:
            if d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _atomic_swap(*, tmp_dir: Path, real_dir: Path) -> None:
    """Two-step rename: real → real.old, tmp → real, rmtree real.old.

    POSIX ``rename(2)`` needs an empty destination, so pre-rename the
    existing real_dir out of the way before moving the tmp dir in. A SIGKILL
    between the two replaces leaves either real_dir intact under ``.old`` or
    the new dir under real_dir — never a half-populated tmp_dir under the
    canonical name.
    """
    old = real_dir.parent / (real_dir.name + ".old")
    # Idempotent cleanup of any leftover ``.old`` from a prior crash.
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)
    if real_dir.exists():
        real_dir.replace(old)
    real_dir.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir.replace(real_dir)
    shutil.rmtree(old, ignore_errors=True)


@contextmanager
def _tavily_off(config: Config) -> Generator[Config]:
    """Yield a ``Config`` copy with ``enable_tavily_synthesis=False`` (Risk 6)."""
    yield config.model_copy(update={"enable_tavily_synthesis": False})


async def record_cassettes_for_inputs(  # noqa: PLR0913 — entry point exposes each knob
    *,
    inputs: list[InputContext],
    output_dir: Path,
    corpus_fixture_path: Path,
    config: Config,
    qdrant_url: str = "http://localhost:6333",
    max_cost_usd: float = _DEFAULT_MAX_COST_USD,
    progress: RecordProgress | None = None,
) -> RecordResult:
    """Record cassettes for every input in ``inputs`` under ``output_dir/<scope>/``.

    Args:
        inputs: One :class:`InputContext` per scope to record.
        output_dir: Parent directory; one subdir per scope (named via
            :func:`slopmortem.evals.runner._row_id`).
        corpus_fixture_path: JSONL fixture used to populate ephemeral Qdrant.
        config: Live config (the helper forces ``enable_tavily_synthesis=False``).
        qdrant_url: Qdrant URL for the ephemeral collection.
        max_cost_usd: Cost ceiling for each per-stage LLM recording wrapper.
        progress: Optional :class:`RecordProgress` sink. Runner wires a Rich
            impl; ``None`` falls back to :class:`NullRecordProgress``.

    Returns:
        :class:`RecordResult` with per-run aggregate counters (rows attempted /
        succeeded, cassettes written to disk, total USD spend). The runner
        feeds these straight into ``render_record_footer``.
    """
    # Lazy imports so import-time cycles stay cheap.
    from slopmortem.cli import _build_deps  # noqa: PLC0415  # pyright: ignore[reportPrivateUsage]
    from slopmortem.corpus.tools_impl import _set_corpus  # noqa: PLC0415

    # ``_row_id`` lives in the runner module; both modules import each other's
    # public surface lazily to avoid an import cycle at process start. Hoisted
    # to function scope (was per-loop) since the lookup is invariant across
    # rows.
    from slopmortem.evals.runner import (  # noqa: PLC0415
        _row_id,  # pyright: ignore[reportPrivateUsage]
    )

    await to_thread.run_sync(lambda: output_dir.mkdir(parents=True, exist_ok=True))
    _sweep_stale_recording_dirs(output_dir, max_age_seconds=_STALE_TMP_SECONDS)

    bar: RecordProgress = progress if progress is not None else NullRecordProgress()
    bar.start_phase(RecordPhase.ROWS, total=len(inputs))
    bar.cost_update(0.0, max_cost_usd)
    running_cost = 0.0
    rows_succeeded = 0
    cassettes_written = 0

    def _on_cost(delta: float) -> None:
        nonlocal running_cost
        running_cost += delta
        bar.cost_update(running_cost, max_cost_usd)

    with _tavily_off(config) as cfg:
        async with setup_ephemeral_qdrant(
            corpus_fixture_path,
            qdrant_url=qdrant_url,
        ) as corpus:
            _set_corpus(corpus)
            llm, embedder, _live_corpus, budget = _build_deps(cfg)
            # We use the helper's ephemeral corpus; ignore _live_corpus.
            del _live_corpus

            for ctx in inputs:
                # Share the directory-naming function with the replay path
                # (`slopmortem.evals.runner._row_id`) so anonymous inputs
                # (`ctx.name == ""`) write to the same `<sha1[:8]>` dir that
                # replay reads from.
                scope_name = _row_id(ctx)
                real_dir = output_dir / scope_name
                tmp_dir = output_dir / f"{scope_name}.{os.getpid()}.{uuid.uuid4().hex}.recording"
                tmp_dir.mkdir(parents=True, exist_ok=False)
                try:
                    rec_llm_facet = RecordingLLMClient(
                        inner=llm,
                        out_dir=tmp_dir,
                        stage="facet_extract",
                        model=cfg.model_facet,
                        max_cost_usd=max_cost_usd,
                        on_cost=_on_cost,
                    )
                    rec_llm_rerank = RecordingLLMClient(
                        inner=llm,
                        out_dir=tmp_dir,
                        stage="llm_rerank",
                        model=cfg.model_rerank,
                        max_cost_usd=max_cost_usd,
                        on_cost=_on_cost,
                    )
                    rec_llm_synth = RecordingLLMClient(
                        inner=llm,
                        out_dir=tmp_dir,
                        stage="synthesize",
                        model=cfg.model_synthesize,
                        max_cost_usd=max_cost_usd,
                        on_cost=_on_cost,
                    )
                    # Each stage uses a different `model` (`model_facet`,
                    # `model_rerank`, `model_synthesize`); the router
                    # dispatches based on the `model=` kwarg the stage
                    # already supplies, so no extra plumbing is needed.
                    routed_llm = _ByModelLLM(
                        {
                            cfg.model_facet: rec_llm_facet,
                            cfg.model_rerank: rec_llm_rerank,
                            cfg.model_synthesize: rec_llm_synth,
                        }
                    )
                    rec_embed = RecordingEmbeddingClient(
                        inner=embedder,
                        out_dir=tmp_dir,
                        on_cost=_on_cost,
                    )

                    from slopmortem.corpus.embed_sparse import (  # noqa: PLC0415
                        encode as live_sparse,
                    )

                    rec_sparse = RecordingSparseEncoder(inner=live_sparse, out_dir=tmp_dir)

                    bridge = _QueryProgressBridge(bar)
                    _ = await run_query(
                        ctx,
                        llm=routed_llm,
                        embedding_client=rec_embed,
                        corpus=corpus,
                        config=cfg,
                        budget=budget,
                        sparse_encoder=rec_sparse,
                        progress=bridge,
                    )
                except Exception:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    raise
                else:
                    _atomic_swap(tmp_dir=tmp_dir, real_dir=real_dir)
                    # Count files actually committed to disk for this row.
                    # Recording wrappers write flat under `real_dir` (no
                    # subdirs), so a non-recursive `glob` is sufficient and
                    # cheaper than `rglob`.
                    n_cassettes = len(list(real_dir.glob("*.json")))
                    cassettes_written += n_cassettes
                    rows_succeeded += 1
                    bar.log(f"✓ {scope_name} — {n_cassettes} cassettes, ${running_cost:.4f}")
                    bar.advance_phase(RecordPhase.ROWS)
    bar.end_phase(RecordPhase.ROWS)
    return RecordResult(
        rows_total=len(inputs),
        rows_succeeded=rows_succeeded,
        cassettes_written=cassettes_written,
        total_cost_usd=running_cost,
    )


class _ByModelLLM:
    """LLM router: dispatch each ``complete()`` to the wrapper for the matching model."""

    def __init__(self, by_model: dict[str, RecordingLLMClient]) -> None:
        """Bind the per-model dispatch table."""
        self._by_model = by_model

    async def complete(  # noqa: PLR0913 — mirrors LLMClient.complete signature
        self,
        prompt: str,
        *,
        system: str | None = None,
        tools: list[Any] | None = None,  # pyright: ignore[reportExplicitAny]
        model: str | None = None,
        cache: bool = False,
        response_format: dict[str, Any] | None = None,  # pyright: ignore[reportExplicitAny]
        extra_body: dict[str, Any] | None = None,  # pyright: ignore[reportExplicitAny]
        max_tokens: int | None = None,
    ) -> CompletionResult:
        """Forward ``complete`` to the wrapper keyed by ``model``.

        Raises:
            KeyError: When ``model`` is ``None`` or not in the dispatch table.
                Callers must always pass an explicit model.
        """
        if model is None or model not in self._by_model:
            msg = f"no recording wrapper for model={model!r}"
            raise KeyError(msg)
        return await self._by_model[model].complete(
            prompt,
            system=system,
            tools=tools,
            model=model,
            cache=cache,
            response_format=response_format,
            extra_body=extra_body,
            max_tokens=max_tokens,
        )


# ``RecordPhase`` doesn't have a direct counterpart for ``QueryPhase.RETRIEVE``;
# retrieve drives the embedding wrapper, which lines up with the EMBED record
# phase. Mapping retrieve→embed inside the bridge keeps both inner-stage
# progress and the embed bar live without adding a separate hook off
# ``RecordingEmbeddingClient``.
_QUERY_TO_RECORD_PHASE: dict[QueryPhase, RecordPhase] = {
    QueryPhase.FACET_EXTRACT: RecordPhase.FACET_EXTRACT,
    QueryPhase.RETRIEVE: RecordPhase.EMBED,
    QueryPhase.RERANK: RecordPhase.RERANK,
    QueryPhase.SYNTHESIZE: RecordPhase.SYNTHESIZE,
}


class _QueryProgressBridge:
    """Forward inner ``QueryPhase`` events to a ``RecordProgress`` sink.

    The recorder runs ``run_query`` once per row; the bridge translates each
    inner phase to the matching :class:`RecordPhase` so all stages share one
    Progress widget. ``start_phase`` resets the underlying task each row so
    the bar restarts at zero rather than accumulating across rows.
    """

    def __init__(self, sink: RecordProgress) -> None:
        """Bind the underlying sink."""
        self._sink = sink

    def start_phase(self, phase: QueryPhase, total: int) -> None:
        """Reset the matching record-phase bar to ``total``."""
        self._sink.start_phase(_QUERY_TO_RECORD_PHASE[phase], total=total)

    def advance_phase(self, phase: QueryPhase, n: int = 1) -> None:
        """Advance the matching record-phase bar by ``n``."""
        self._sink.advance_phase(_QUERY_TO_RECORD_PHASE[phase], n=n)

    def end_phase(self, phase: QueryPhase) -> None:
        """End the matching record-phase bar."""
        self._sink.end_phase(_QUERY_TO_RECORD_PHASE[phase])

    def set_phase_status(self, phase: QueryPhase, status: str | None) -> None:
        """Set transient status on the matching record-phase bar."""
        self._sink.set_phase_status(_QUERY_TO_RECORD_PHASE[phase], status)

    def log(self, message: str) -> None:
        """Forward a one-off status line."""
        self._sink.log(message)

    def error(self, phase: QueryPhase, message: str) -> None:
        """Forward an error against the matching record phase."""
        self._sink.error(_QUERY_TO_RECORD_PHASE[phase], message)
