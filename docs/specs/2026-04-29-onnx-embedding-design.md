# Local ONNX embedding via fastembed — design

**Date:** 2026-04-29
**Status:** draft (pending review)
**Topic:** Add a local ONNX-runtime embedding provider (fastembed) alongside the existing OpenAI client, switchable via config, and flip the default to local. No changes to the `EmbeddingClient` Protocol or to any consumer (ingest, retrieve, evals).

## Goal

Today every embedding call goes through `OpenAIEmbeddingClient`, which requires a network round-trip and an `openai_api_key`. This couples local development, CI, and production to OpenAI's availability and per-token cost. The codebase already has a clean `EmbeddingClient` Protocol (`slopmortem/llm/embedding_client.py:20`) with two implementations (`OpenAIEmbeddingClient`, `FakeEmbeddingClient`), and the Qdrant collection is created with `dim` read off the embedder rather than hardcoded — so adding a third provider is a localized change.

After implementation:

- `embedding_provider="fastembed"` is the default; `embed_model_id="nomic-embed-text-v1.5"` (768-d, 8192-token context, MTEB ≈ `text-embedding-3-small`)
- `embedding_provider="openai"` still works for users who want OpenAI quality
- Ingest and retrieve run fully offline once the model file is cached locally
- A new `slopmortem embed-prefetch` CLI subcommand warms the model cache for CI

## Execution Strategy

**Parallel subagents** (sequential dispatch per user preference). Four small Python-only tasks with disjoint file ownership; per-task review is sufficient and the persistent-team coordination overhead of `/team-feature` would not pay off at this size. Each subagent runs to completion and is reviewed before the next dispatches; subagents do not run `git add` or `git commit` — the parent owns commit authorship.

## Agent Assignments

| Task | Agent | Domain |
|------|-------|--------|
| 1. Add `fastembed` dep + new `FastEmbedEmbeddingClient` + `EMBED_DIMS` entry + empty-input short-circuit on `OpenAIEmbeddingClient` for symmetry | python-development:python-pro | Python |
| 2. Config defaults flip in `slopmortem/config.py` + `embed_cache_dir` knob + matching update to `slopmortem.toml` (lines 18–19) so the TOML override doesn't silently keep `"openai"` as the effective default | python-development:python-pro | Python |
| 3. CLI factory (`_make_embedder`) replacing inline `OpenAIEmbeddingClient(...)` construction at `slopmortem/cli.py:362` (inside `_build_deps`) and `slopmortem/cli.py:443` (sibling construction in the ingest-cost variant) + new `embed-prefetch` subcommand | python-development:python-pro | Python |
| 4. New `tests/llm/test_fastembed_client.py` and `tests/llm/test_embedder_factory.py`. No edits expected to existing default-assertion tests — `tests/llm/test_embeddings.py` hardcodes `"text-embedding-3-small"` because it tests `OpenAIEmbeddingClient` specifically, not config defaults; ingest tests already read `cfg.embed_model_id` rather than asserting a literal | python-development:python-pro | Python |

## Decisions and trade-offs

### Model: `nomic-embed-text-v1.5`

- **Pros:** 8192-token context fits the existing 768-token chunker with room to spare (`slopmortem/corpus/chunk.py:20`); MTEB on retrieval tasks is within noise of `text-embedding-3-small`; Apache-2.0 license; 768-d vectors are smaller than the current 1536-d, so Qdrant search and storage shrink ~50%.
- **Cons:** ~550MB ONNX file; ~3× slower per chunk than `bge-small-en-v1.5` on CPU.
- **Why:** BGE-family models cap at 512 tokens. Using `bge-small` would force a corpus re-chunk and re-ingest, which is more disruptive than absorbing the throughput hit on a model that handles existing chunks unchanged. `bge-large-en-v1.5` is 1.3GB and slower with no quality win at the 768-d level. `all-MiniLM-L6-v2` is small and fast but ~6 MTEB points behind, which matters for retrieval quality.

### Runtime: `fastembed` (Qdrant's ONNX wrapper)

- **Pros:** Bundled int8-quantized ONNX variants give ~2× CPU throughput out of the box; tokenizer + onnxruntime + model download all handled by one library; tested against Qdrant's own embedding shapes.
- **Cons:** Curated model list — adding an arbitrary HuggingFace model means waiting for upstream support or forking; quantized ONNX is fastembed's conversion, not the canonical HF release, so reproducing exact vectors elsewhere is harder; sync API requires `anyio.to_thread.run_sync` to honor the project's async contract.
- **Why:** Throughput is on par with hand-rolling `onnxruntime` + HF `tokenizers` (same kernels, same hot path), and the curation downsides only bite if we ever need a model fastembed doesn't ship — which we don't today. Going lower-level would mean reimplementing model download, tokenizer setup, and pooling/normalization for marginal control benefit.

### Provider selection: keep both, default to fastembed

- **Pros:** Users with an OpenAI key can opt in to `text-embedding-3-large` quality or use OpenAI when fastembed has a model bug; the existing `OpenAIEmbeddingClient` test surface stays intact.
- **Cons:** Two implementations to keep aligned on edge cases (empty input, model-name validation); the budget reserve/settle path stays even though it's a no-op for fastembed.
- **Why:** The `EmbeddingClient` Protocol already supports multiple backends — `FakeEmbeddingClient` proves it. Adding a third doesn't grow the contract; it grows the registry of implementations behind it.

### Migration: clean break

- **Pros:** No migration code; no auto-rebuild on dim mismatch; one config flip and a re-ingest.
- **Cons:** Anyone with an existing 1536-d Qdrant collection has to drop and re-run `slopmortem ingest`.
- **Why:** No production deployment exists to migrate. Side-by-side collections (per-model collection names) and auto-rebuild-on-dim-mismatch both add code that exists only to handle a state nobody is in.

## Architecture

A new `FastEmbedEmbeddingClient` implements the existing `EmbeddingClient` Protocol. The Protocol stays untouched, and `ingest.py`, `pipeline.py`, `retrieve.py`, and `qdrant_store.py` all keep working unchanged — they consume the Protocol and read `dim` off the client instance.

Provider selection lives in a `_make_embedder(config, budget) -> EmbeddingClient` factory in `slopmortem/cli.py`, replacing the inline `OpenAIEmbeddingClient(...)` construction at `slopmortem/cli.py:362` (inside `_build_deps`, the shared dep-builder used by `:297` and `:495`) and `slopmortem/cli.py:443` (a sibling construction in the ingest-cost-variant function around `:426`). Both call sites construct `OpenAIEmbeddingClient` directly today; both get replaced with `_make_embedder(config, budget)`. The factory branches on `config.embedding_provider`:

```
"openai"    -> OpenAIEmbeddingClient(sdk=AsyncOpenAI(...), budget=budget, model=...)
"fastembed" -> FastEmbedEmbeddingClient(model=config.embed_model_id, budget=budget, cache_dir=config.embed_cache_dir)
```

Unknown provider names raise a `ValueError` with the list of valid choices. The default config flips to `embedding_provider="fastembed"` and `embed_model_id="nomic-embed-text-v1.5"`. `EMBED_DIMS` extends with `"nomic-embed-text-v1.5": 768`.

## Components

### New: `slopmortem/llm/fastembed_client.py`

```python
class FastEmbedEmbeddingClient:
    def __init__(self, *, model: str, budget: Budget, cache_dir: Path | None = None) -> None: ...
    @property
    def dim(self) -> int: ...
    async def embed(self, texts: list[str], *, model: str | None = None) -> EmbeddingResult: ...
```

`budget` is required (not `Budget | None`) for contract symmetry with `OpenAIEmbeddingClient` — every embed call routes through the budget per the comment at `slopmortem/ingest.py:33`. The amounts are zero, but the call is real, so tracing/observability hooks that wrap `reserve`/`settle` keep firing.

- Wraps `fastembed.TextEmbedding(model_name=...)`. The fastembed API is sync; `embed()` calls `anyio.to_thread.run_sync` to honor the async contract used by every other client in `slopmortem/llm/`.
- The fastembed model object is lazy-loaded on first `embed()` call so `__init__` is cheap, doesn't touch disk in tests, and doesn't download anything until actually needed. A guarded private method `_ensure_loaded()` materializes it on demand and is also called by the `embed-prefetch` CLI command (see Task 3).
- `dim` reads from `EMBED_DIMS[self.model]`, the same registry `OpenAIEmbeddingClient` uses.
- Returns `EmbeddingResult(vectors, n_tokens, cost_usd=0.0)`. `n_tokens` is the sum of token-id lengths from fastembed's internal tokenizer; `cost_usd` is always zero.

### Modified: `slopmortem/config.py`

- `embedding_provider: str = "fastembed"` (was `"openai"`).
- `embed_model_id: str = "nomic-embed-text-v1.5"` (was `"text-embedding-3-small"`).
- New: `embed_cache_dir: Path | None = None` — optional override for fastembed's model cache directory; `None` lets fastembed pick (`~/.cache/fastembed`).

### Modified: `slopmortem.toml`

- Lines 18–19 currently pin `embedding_provider = "openai"` and `embed_model_id = "text-embedding-3-small"`. The TOML source overrides env per `Config.settings_customise_sources`, so without updating the TOML the new `config.py` defaults would be ignored at runtime. Flip both lines to the new defaults (`"fastembed"`, `"nomic-embed-text-v1.5"`) so the user-visible config matches code.

### Modified: `slopmortem/cli.py`

- New private function `_make_embedder(config, budget) -> EmbeddingClient` replaces inline construction at `:362` (inside `_build_deps`) and `:443` (the ingest-cost sibling). Both sites construct `OpenAIEmbeddingClient` directly today.
- New subcommand `slopmortem embed-prefetch`: constructs the configured embedder and calls `_ensure_loaded()` (or the public equivalent), so CI can prime the model cache before running ingest. Returns a non-zero exit on failure with a clear error message.

### Modified: `slopmortem/llm/__init__.py`

- Export `FastEmbedEmbeddingClient` alongside `OpenAIEmbeddingClient`.

### Modified: `pyproject.toml`

- Add `fastembed` as a runtime dependency. Pin a recent stable version; verify `nomic-embed-text-v1.5` is in its supported model list at the chosen pin.

### Unchanged: `FakeEmbeddingClient`

Stays as-is for evals and tests. fastembed is deterministic, but loading a 550MB model and running ONNX inference is slower than the synthetic-vector fake, and the eval cassette flow assumes vector hashing not real model output. The fake remains the correct choice for those paths.

## Data flow

**Ingest** (`slopmortem/ingest.py`, unchanged):

```
chunks -> embed_client.embed(texts) -> EmbeddingResult{vectors, n_tokens, cost_usd}
       -> qdrant_store.upsert(vectors, ...)
```

For fastembed, vectors come out of onnxruntime via `run_sync`, `n_tokens` is summed from the tokenizer, `cost_usd = 0.0`.

**Retrieve** (`slopmortem/stages/retrieve.py`, unchanged):

```
query -> embed_client.embed([query]) -> vectors[0] -> qdrant search
```

Same code, hitting the local model instead of the network.

**Budget interaction:**

`FastEmbedEmbeddingClient.embed()` still calls `budget.reserve(0.0)` and `budget.settle(rid, 0.0)`. This keeps the contract uniform across providers — the comment at `slopmortem/ingest.py:33` says every LLM and embedding call routes through the budget, and we preserve that. Tracing/observability hooks that wrap `reserve`/`settle` keep firing.

**Lazy load:**

First `embed()` call triggers `fastembed.TextEmbedding(model_name=...)` inside `run_sync`. ~2s on the first call (model decode + warmup), then per-batch latency only. The model is held as an instance attribute and reused across calls.

## Error handling

**Model load failures.** fastembed raises on `TextEmbedding(model_name=...)` if the cache is corrupt or the download fails. The lazy load wraps that in a `try/except` that converts to a clear `RuntimeError("fastembed model {name} failed to load: {detail}; try running 'slopmortem embed-prefetch'")`. No retry — a failed load is deterministic.

**Unknown model name.** Mirrors `OpenAIEmbeddingClient`: if `model` isn't in `EMBED_DIMS`, raise in `__init__` with a message naming the dict to update. Catches typos before any disk I/O.

**Empty input.** `embed([])` returns `EmbeddingResult(vectors=[], n_tokens=0, cost_usd=0.0)` without invoking fastembed and without loading the model. Task 1 also adds the same short-circuit to `OpenAIEmbeddingClient` (the OpenAI SDK errors on empty `input` today) so both clients share identical empty-input semantics.

**Tokenizer overflow.** nomic's cap is 8192 tokens; the chunker emits 768-token windows. Overflow can't happen in normal use. fastembed truncates silently if it ever did, which we accept as the documented behavior.

**No transient retry loop.** `OpenAIEmbeddingClient` retries on transient HTTP errors. Local inference has no transient failures — either the model loads or it doesn't. The two clients diverge here intentionally, and the divergence is worth a one-line comment on the Protocol so a future third provider doesn't copy the wrong template.

## Testing

**New: `tests/llm/test_fastembed_client.py`**

- `dim` matches `EMBED_DIMS["nomic-embed-text-v1.5"]` (= 768).
- `embed(["hello", "world"])` returns 2 vectors of length 768; vectors are L2-normalized (assert this so a fastembed change can't silently break cosine similarity in Qdrant).
- `embed([])` returns empty result without loading the model — assert no model attribute is set after the call.
- Unknown model name in `__init__` raises with a message naming `EMBED_DIMS`.
- `cost_usd == 0.0` always; `n_tokens > 0` for non-empty input.
- Tests that load the model are marked `@pytest.mark.slow` (or the project equivalent) so the fast CI lane skips the ~550MB download.

**New: `tests/llm/test_embedder_factory.py`**

- `_make_embedder()` returns `OpenAIEmbeddingClient` for `"openai"`, `FastEmbedEmbeddingClient` for `"fastembed"`, raises `ValueError` for unknown providers.
- Uses a stub `Budget`; does not load any model.

**Unchanged: `tests/llm/test_embeddings.py`**

- The literal `"text-embedding-3-small"` references in this file (`:46`, `:58`, `:60`, `:71`, `:101`, `:123`, `:141`, `:142`, `:149`, `:157`) test `OpenAIEmbeddingClient` and `FakeEmbeddingClient` directly with explicit model arguments — they are not assertions about config defaults. They stay as-is.

**Unchanged: ingest tests** (`tests/test_ingest_*.py`)

- Eight call sites read `cfg.embed_model_id` rather than asserting a literal model name, so they pick up the new default automatically without edits.

**Unchanged:** `tests/test_pipeline_e2e.py`, `tests/test_observe_redaction.py`, ingest tests, and `slopmortem/evals/runner.py:94` continue to use `FakeEmbeddingClient`. The new client is not a substitute for the fake in offline e2e/eval paths.

**Manual check** (documented in PR description, not in CI): `slopmortem ingest` against a small fixture corpus with the new default, then `slopmortem ask` to confirm retrieve still returns sensible results.

## Out of scope

- Side-by-side per-model collections in Qdrant.
- Auto-rebuild on dim mismatch.
- A third embedding provider (Ollama, raw onnxruntime, sentence-transformers).
- Eager model load at CLI startup. The 2s lazy-load delay lands inside the first `embed()` call; if it becomes annoying we can move it into `_make_embedder` later.
- Eval cassettes for fastembed output. Evals continue to use `FakeEmbeddingClient`; cassetting real onnxruntime output adds complexity without changing what the evals validate.
