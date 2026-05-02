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

import anyio
from anyio import to_thread
from openai import AsyncOpenAI

from slopmortem.budget import Budget
from slopmortem.concurrency import gather_resilient
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
from slopmortem.llm.embedding_factory import make_embedder
from slopmortem.llm.openrouter import OpenRouterClient
from slopmortem.pipeline import QueryPhase, run_query

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from slopmortem.config import Config
    from slopmortem.llm.client import CompletionResult
    from slopmortem.models import InputContext


_DEFAULT_MAX_COST_USD = 2.0
_STALE_TMP_SECONDS = 24 * 3600
_DEFAULT_MAX_CONCURRENT_ROWS = 3
# Per-row pipeline emits one advance for FACET_EXTRACT, RETRIEVE, RERANK each,
# plus up to ``N_synthesize`` for SYNTHESIZE. Used to size the aggregate bar so
# percentage tracks subtask completion, not just row coarse counts.
_FIXED_TICKS_PER_ROW = 3


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
    max_concurrent_rows: int = _DEFAULT_MAX_CONCURRENT_ROWS,
) -> RecordResult:
    """Record cassettes for every input in ``inputs`` under ``output_dir/<scope>/``.

    Rows run in parallel under an ``anyio.CapacityLimiter(max_concurrent_rows)``
    so a 10-row run finishes in ~``ceil(N/limit)`` row-times instead of N.
    Per-row deps (Budget, OpenRouterClient, embedder) are shared across rows
    — per-row cost ceilings are still enforced by the recording wrappers'
    ``max_cost_usd``. Failed rows clean their tmp dirs; the first failure is
    re-raised after every other row settles.

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
        max_concurrent_rows: Upper bound on rows running concurrently. Default
            3 keeps total in-flight Sonnet calls (~`limit × (3 + N_synthesize)`)
            comfortably under typical OpenRouter per-key rate limits.

    Returns:
        :class:`RecordResult` with per-run aggregate counters (rows attempted /
        succeeded, cassettes written to disk, total USD spend). The runner
        feeds these straight into ``render_record_footer``.
    """
    # Lazy imports so import-time cycles stay cheap.
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
    ticks_per_row = _FIXED_TICKS_PER_ROW + config.N_synthesize
    bar.start_phase(RecordPhase.ROWS, total=len(inputs) * ticks_per_row)
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
            # Shared deps: SDK, Budget, OpenRouterClient, embedder. Per-row
            # spend caps are enforced by each per-stage RecordingLLMClient
            # via its own ``max_cost_usd``; the inner Budget is sized to the
            # worst-case run total so a single shared instance never starves
            # a row mid-stage.
            budget = Budget(
                cap_usd=max(
                    cfg.max_cost_usd_per_query,
                    max_cost_usd * (_FIXED_TICKS_PER_ROW) * len(inputs),
                )
            )
            openrouter_sdk = AsyncOpenAI(
                api_key=cfg.openrouter_api_key.get_secret_value(),
                base_url=cfg.openrouter_base_url,
            )
            llm = OpenRouterClient(
                sdk=openrouter_sdk,
                budget=budget,
                model=cfg.model_synthesize,
            )
            embedder = make_embedder(cfg, budget)

            from slopmortem.corpus.embed_sparse import (  # noqa: PLC0415
                encode as live_sparse,
            )

            limiter = anyio.CapacityLimiter(max_concurrent_rows)

            async def _record_row(ctx: InputContext) -> None:
                nonlocal cassettes_written, rows_succeeded
                async with limiter:
                    # Share the directory-naming function with the replay path
                    # (`slopmortem.evals.runner._row_id`) so anonymous inputs
                    # (`ctx.name == ""`) write to the same `<sha1[:8]>` dir
                    # that replay reads from.
                    scope_name = _row_id(ctx)
                    real_dir = output_dir / scope_name
                    tmp_dir = (
                        output_dir / f"{scope_name}.{os.getpid()}.{uuid.uuid4().hex}.recording"
                    )
                    tmp_dir.mkdir(parents=True, exist_ok=False)
                    bridge = _AggregateProgressBridge(bar, ticks_per_row=ticks_per_row)
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
                        rec_sparse = RecordingSparseEncoder(inner=live_sparse, out_dir=tmp_dir)

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
                    except Exception as exc:
                        shutil.rmtree(tmp_dir, ignore_errors=True)
                        bar.error(RecordPhase.ROWS, f"{scope_name}: {exc!r}")
                        bridge.top_up()
                        raise
                    else:
                        _atomic_swap(tmp_dir=tmp_dir, real_dir=real_dir)
                        # Count files actually committed to disk for this row.
                        # Recording wrappers write flat under `real_dir` (no
                        # subdirs), so a non-recursive `glob` is sufficient
                        # and cheaper than `rglob`.
                        n_cassettes = len(list(real_dir.glob("*.json")))
                        cassettes_written += n_cassettes
                        rows_succeeded += 1
                        bar.log(f"✓ {scope_name} — {n_cassettes} cassettes, ${running_cost:.4f}")
                        bridge.top_up()

            results = await gather_resilient(*(_record_row(ctx) for ctx in inputs))
            # gather_resilient runs every row to completion; surface the
            # first exception so partial-failure runs still error out (parity
            # with the prior fail-fast loop). Successful rows have already
            # atomic-swapped their cassette dirs and stay on disk.
            for r in results:
                if isinstance(r, BaseException):
                    raise r
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


class _AggregateProgressBridge:
    """Funnel one row's inner :class:`QueryPhase` advances into the shared ROWS bar.

    Each parallel row gets its own bridge; every bridge advances the same
    ``RecordPhase.ROWS`` task on the shared :class:`RecordProgress` sink. The
    sink's total is pre-summed at the helper level as
    ``len(inputs) * (3 + N_synthesize)``, so the percentage tracks subtask
    completion instead of stepping in row-sized chunks. Inline phase detail
    (``set_phase_status``) is dropped under parallel mode — concurrent rows
    posting to one status line would race and produce gibberish.

    Rows that retrieve fewer than ``N_synthesize`` candidates fire fewer
    SYNTHESIZE advances than budgeted; :meth:`top_up` settles the difference
    at row completion so the bar always reaches its declared total.
    """

    def __init__(self, sink: RecordProgress, ticks_per_row: int) -> None:
        """Bind the shared sink and the per-row tick budget."""
        self._sink = sink
        self._ticks_per_row = ticks_per_row
        self._ticked = 0

    def start_phase(self, phase: QueryPhase, total: int) -> None:
        """No-op; phase totals are pre-summed at the helper level."""
        del phase, total

    def advance_phase(self, phase: QueryPhase, n: int = 1) -> None:
        """Forward each inner advance to the shared ROWS bar."""
        del phase
        delta = min(n, self._ticks_per_row - self._ticked)
        if delta <= 0:
            return
        self._ticked += delta
        self._sink.advance_phase(RecordPhase.ROWS, delta)

    def end_phase(self, phase: QueryPhase) -> None:
        """No-op."""
        del phase

    def set_phase_status(self, phase: QueryPhase, status: str | None) -> None:
        """No-op under parallel mode — inline detail would race across rows."""
        del phase, status

    def log(self, message: str) -> None:
        """Forward a one-off status line."""
        self._sink.log(message)

    def error(self, phase: QueryPhase, message: str) -> None:
        """Forward an error; attribute it to ``ROWS`` since inner phases have no task."""
        del phase
        self._sink.error(RecordPhase.ROWS, message)

    def top_up(self) -> None:
        """Advance any ticks the row didn't fire so the bar reaches its declared total."""
        remaining = self._ticks_per_row - self._ticked
        if remaining > 0:
            self._sink.advance_phase(RecordPhase.ROWS, remaining)
            self._ticked = self._ticks_per_row
