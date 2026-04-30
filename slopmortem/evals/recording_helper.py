"""Record cassettes for a set of inputs end to end.

Handles the ephemeral Qdrant lifecycle, the recording wrappers, the two-step
atomic dir swap, and forces Tavily off. Test authors call this when they
want a per-test cassette set without re-implementing the plumbing.
"""

from __future__ import annotations

import os
import shutil
import time
import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from anyio import to_thread

from slopmortem.evals.qdrant_setup import setup_ephemeral_qdrant
from slopmortem.evals.recording import (
    RecordingEmbeddingClient,
    RecordingLLMClient,
    RecordingSparseEncoder,
)
from slopmortem.pipeline import run_query

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from slopmortem.config import Config
    from slopmortem.llm.client import CompletionResult
    from slopmortem.models import InputContext


_DEFAULT_MAX_COST_USD = 2.0
_STALE_TMP_SECONDS = 24 * 3600


def _sweep_stale_recording_dirs(root: Path, *, max_age_seconds: int) -> None:
    """Remove ``*.recording`` dirs older than ``max_age_seconds`` under ``root``.

    Conservative best-effort: any stat/rm failure is swallowed. Ignored
    silently when ``root`` does not yet exist.
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
    """Two-step rename: real -> real.old, tmp -> real, rmtree real.old.

    POSIX ``rename(2)`` requires an empty destination, so we pre-rename the
    existing real_dir out of the way before moving the tmp dir in. A
    SIGKILL between the two replaces leaves either real_dir intact under
    ``.old`` or the new dir under real_dir; never a half-populated tmp_dir
    under the canonical name.
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
) -> None:
    """Record cassettes for every input in ``inputs`` under ``output_dir/<scope>/``.

    Args:
        inputs: One :class:`InputContext` per scope to record.
        output_dir: Parent directory; one subdir per scope (named via
            :func:`slopmortem.evals.runner._row_id`).
        corpus_fixture_path: JSONL fixture used to populate ephemeral Qdrant.
        config: Live config (the helper forces ``enable_tavily_synthesis=False``).
        qdrant_url: Qdrant URL for the ephemeral collection.
        max_cost_usd: Cost ceiling for each per-stage LLM recording wrapper.
    """
    # Lazy imports so import-time cycles stay cheap.
    from slopmortem.cli import _build_deps  # noqa: PLC0415  # pyright: ignore[reportPrivateUsage]
    from slopmortem.corpus.tools_impl import _set_corpus  # noqa: PLC0415

    await to_thread.run_sync(lambda: output_dir.mkdir(parents=True, exist_ok=True))
    _sweep_stale_recording_dirs(output_dir, max_age_seconds=_STALE_TMP_SECONDS)

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
                from slopmortem.evals.runner import (  # noqa: PLC0415
                    _row_id,  # pyright: ignore[reportPrivateUsage]
                )

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
                    )
                    rec_llm_rerank = RecordingLLMClient(
                        inner=llm,
                        out_dir=tmp_dir,
                        stage="llm_rerank",
                        model=cfg.model_rerank,
                        max_cost_usd=max_cost_usd,
                    )
                    rec_llm_synth = RecordingLLMClient(
                        inner=llm,
                        out_dir=tmp_dir,
                        stage="synthesize",
                        model=cfg.model_synthesize,
                        max_cost_usd=max_cost_usd,
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
                    rec_embed = RecordingEmbeddingClient(inner=embedder, out_dir=tmp_dir)

                    from slopmortem.corpus.embed_sparse import (  # noqa: PLC0415
                        encode as live_sparse,
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
                    )
                except Exception:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    raise
                else:
                    _atomic_swap(tmp_dir=tmp_dir, real_dir=real_dir)


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
            KeyError: When ``model`` is ``None`` or not in the dispatch table —
                callers must always pass an explicit model.
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
