# Local ONNX embedding via fastembed — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or `/team-feature` to implement this plan task-by-task, per the Execution Strategy below. Steps use checkbox (`- [ ]`) syntax — these are **persistent durable state**, not visual decoration. The executor edits the plan file in place: `- [ ]` → `- [x]` the instant a step verifies, before moving on. On resume (new session, crash, takeover), the executor scans existing `- [x]` marks and skips them — these steps are NOT redone. TodoWrite mirrors this state in-session; the plan file is the source of truth across sessions.

**Goal:** Add a `FastEmbedEmbeddingClient` (ONNX/fastembed) alongside the existing `OpenAIEmbeddingClient`, switchable via `embedding_provider`, and flip the default to `"fastembed"` with `nomic-ai/nomic-embed-text-v1.5` so ingest, retrieve, and CI run offline.

**Architecture:** A new `slopmortem/llm/fastembed_client.py` implements the existing `EmbeddingClient` Protocol with an anyio-thread-wrapped fastembed `TextEmbedding`, lazy model load, and a budget contract that reserves/settles zero. CLI dependency wiring moves from inline `OpenAIEmbeddingClient(...)` calls to a `_make_embedder(config, budget)` factory; an `embed-prefetch` subcommand warms the cache for CI. Defaults flip in `slopmortem/config.py` and the matching `slopmortem.toml` lines.

**Tech Stack:** Python 3.14, `fastembed>=0.8` (already declared in `pyproject.toml`), `anyio.to_thread.run_sync`, pydantic-settings, typer, pytest (asyncio auto mode).

## Execution Strategy

**Parallel subagents** (sequential dispatch per user preference). Four small Python-only tasks with disjoint file ownership; per-task review is sufficient and the persistent-team coordination overhead of `/team-feature` would not pay off at this size. Each subagent runs to completion and is reviewed before the next dispatches; subagents do not run `git add` or `git commit` — the parent owns commit authorship.

## Agent Assignments

- Task 1: `FastEmbedEmbeddingClient` + `EMBED_DIMS` entry + `OpenAIEmbeddingClient` empty-input short-circuit → python-development:python-pro (Python)
- Task 2: Config defaults flip + `embed_cache_dir` knob + `slopmortem.toml` lines 18–19 → python-development:python-pro (Python)
- Task 3: CLI `_make_embedder` factory + `embed-prefetch` subcommand → python-development:python-pro (Python)
- Task 4: New tests for `FastEmbedEmbeddingClient` and the factory → python-development:python-pro (Python)

---

## File Structure

**Create:**
- `slopmortem/llm/fastembed_client.py` — new `FastEmbedEmbeddingClient` (Task 1)
- `tests/llm/test_fastembed_client.py` — unit tests for the new client (Task 4)
- `tests/llm/test_embedder_factory.py` — unit tests for `_make_embedder` (Task 4)

**Modify:**
- `slopmortem/llm/openai_embeddings.py` — add empty-input short-circuit + new `EMBED_DIMS` row (Task 1)
- `slopmortem/llm/__init__.py` — re-export `FastEmbedEmbeddingClient` (Task 1)
- `pyproject.toml` — already declares `fastembed>=0.8`; verify and refresh lock (Task 1)
- `slopmortem/config.py` — flip defaults; add `embed_cache_dir` (Task 2)
- `slopmortem.toml` — flip the user-facing TOML defaults so they match `config.py` (Task 2)
- `slopmortem/cli.py` — replace inline `OpenAIEmbeddingClient(...)` at `:362` (`_build_deps`) and `:443` (`_build_ingest_deps`) with `_make_embedder(config, budget)`; add `embed-prefetch` subcommand (Task 3)

**Unchanged:**
- `slopmortem/llm/embedding_client.py` — Protocol stays as-is
- `slopmortem/llm/fake_embeddings.py` — fake client unchanged
- `slopmortem/ingest.py`, `slopmortem/pipeline.py`, `slopmortem/stages/retrieve.py`, `slopmortem/corpus/qdrant_store.py` — all consume the Protocol
- `tests/llm/test_embeddings.py` — its `"text-embedding-3-small"` literals exercise the OpenAI client directly, not config defaults
- All `tests/test_ingest_*.py` — they read `cfg.embed_model_id` not literals

---

## Task 1: `FastEmbedEmbeddingClient` + dim registry + symmetric empty-input

**Files:**
- Create: `slopmortem/llm/fastembed_client.py`
- Modify: `slopmortem/llm/openai_embeddings.py` (add `nomic-ai/nomic-embed-text-v1.5` to `EMBED_DIMS`; add empty-input short-circuit in `embed`)
- Modify: `slopmortem/llm/__init__.py` (re-export the new class)
- Modify: `pyproject.toml` (`fastembed>=0.8` already present — verify, then refresh `uv.lock`)

**Context for the implementer:**

- `EMBED_DIMS` lives in `slopmortem/llm/openai_embeddings.py:25`. It is the single registry both `OpenAIEmbeddingClient` and `FakeEmbeddingClient` (`slopmortem/llm/fake_embeddings.py:9`) import from.
- The `EmbeddingClient` Protocol (`slopmortem/llm/embedding_client.py:18`) requires only `async def embed(texts, *, model=None) -> EmbeddingResult`. `dim` is not part of the Protocol but every implementation exposes it as a `@property`; `slopmortem/corpus/qdrant_store.py:49` reads it.
- `Budget` (`slopmortem/budget.py:16`) exposes `reserve(amount_usd) -> rid` and `settle(rid, actual_usd) -> None`. Reserving 0.0 is legal; the call still acquires/releases the lock and lets tracing hooks fire.
- `fastembed.TextEmbedding(model_name=...)` is a sync API. Wrap it with `anyio.to_thread.run_sync` (already used elsewhere in the codebase — see `slopmortem/ingest.py:49`).
- The fastembed model is ~550 MB on disk. The lazy-load contract (no disk I/O in `__init__`) lets CLI smoke tests construct the client without downloading.

- [ ] **Step 1.1: Write the failing dim test**

Create `tests/llm/test_fastembed_client.py` (the file is owned by Task 4 but a single dim assertion lives here as the TDD entrypoint for Task 1; Task 4 expands it). Add:

```python
from __future__ import annotations

import pytest

from slopmortem.budget import Budget
from slopmortem.llm.fastembed_client import FastEmbedEmbeddingClient


def test_dim_matches_embed_dims_registry():
    c = FastEmbedEmbeddingClient(model="nomic-ai/nomic-embed-text-v1.5", budget=Budget(0.0))
    assert c.dim == 768
```

`pytest` is imported up-front so subsequent Task 4 steps can append tests that use `pytest.raises` without inserting a mid-file import (which would trip ruff `E402`).

- [ ] **Step 1.2: Run the test and verify it fails**

Run: `uv run pytest tests/llm/test_fastembed_client.py::test_dim_matches_embed_dims_registry -v`
Expected: FAIL with `ModuleNotFoundError: slopmortem.llm.fastembed_client`.

- [ ] **Step 1.3: Add `nomic-ai/nomic-embed-text-v1.5` to `EMBED_DIMS` and split the OpenAI-allowed-model set**

`EMBED_DIMS` is the shared dimensionality registry consumed by both the OpenAI client and the new fastembed client. After this change it also contains `nomic-ai/nomic-embed-text-v1.5`, which `OpenAIEmbeddingClient` cannot price or serve. To keep the `__init__` guard strict, introduce a separate `OPENAI_EMBED_MODELS` constant and have the OpenAI client validate against it instead.

Edit `slopmortem/llm/openai_embeddings.py:25-28`. Replace the dict with:

```python
EMBED_DIMS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "nomic-ai/nomic-embed-text-v1.5": 768,
}

OPENAI_EMBED_MODELS: frozenset[str] = frozenset({
    "text-embedding-3-small",
    "text-embedding-3-large",
})
```

Then in the same file at line 59, change:

```python
        if model not in EMBED_DIMS:
            msg = f"unknown embed model {model!r}; add it to EMBED_DIMS"
            raise ValueError(msg)
```

to:

```python
        if model not in OPENAI_EMBED_MODELS:
            msg = (
                f"OpenAIEmbeddingClient does not support model {model!r}; "
                f"valid choices: {sorted(OPENAI_EMBED_MODELS)}"
            )
            raise ValueError(msg)
```

This keeps `EMBED_DIMS` as the shared dim registry (read by `dim`, the qdrant collection sizing, and the fastembed client) while preventing a misconfigured `OpenAIEmbeddingClient(model="nomic-ai/...")` from constructing successfully and crashing later inside `_input_rate_per_million`.

- [ ] **Step 1.4: Create `slopmortem/llm/fastembed_client.py` with the minimal class**

Write the full implementation (all subsequent tests in Task 4 will need it):

```python
"""Local ONNX embedding client backed by fastembed; mirrors the OpenAI client contract."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from anyio import to_thread

from slopmortem.llm.embedding_client import EmbeddingResult
from slopmortem.llm.openai_embeddings import EMBED_DIMS

if TYPE_CHECKING:
    from slopmortem.budget import Budget


class FastEmbedEmbeddingClient:
    """ONNX-backed EmbeddingClient that runs locally and settles zero against the budget."""

    def __init__(
        self,
        *,
        model: str,
        budget: Budget,
        cache_dir: Path | None = None,
    ) -> None:
        """Bind the model name and budget; defer fastembed import and model load until first embed."""
        if model not in EMBED_DIMS:
            msg = f"unknown embed model {model!r}; add it to EMBED_DIMS"
            raise ValueError(msg)
        self.model = model
        self._budget = budget
        self._cache_dir = cache_dir
        self._te: object | None = None  # fastembed.TextEmbedding instance, lazy

    @property
    def dim(self) -> int:
        """Vector dimensionality for the configured embedding model."""
        return EMBED_DIMS[self.model]

    async def _ensure_loaded(self) -> object:
        """Materialize the fastembed model on first use; idempotent."""
        if self._te is not None:
            return self._te
        self._te = await to_thread.run_sync(self._load_sync)
        return self._te

    def _load_sync(self) -> object:
        from fastembed import TextEmbedding  # noqa: PLC0415 — heavy import, defer

        kwargs: dict[str, object] = {"model_name": self.model, "lazy_load": True}
        if self._cache_dir is not None:
            kwargs["cache_dir"] = str(self._cache_dir)
        try:
            return TextEmbedding(**kwargs)
        except Exception as exc:
            msg = (
                f"fastembed model {self.model!r} failed to load: {exc}; "
                f"try running 'slopmortem embed-prefetch'"
            )
            raise RuntimeError(msg) from exc

    async def embed(
        self, texts: list[str], *, model: str | None = None
    ) -> EmbeddingResult:
        """Embed *texts* locally; budget reserve/settle 0.0 for contract symmetry."""
        if model is not None and model != self.model:
            msg = (
                f"FastEmbedEmbeddingClient was constructed with {self.model!r}; "
                f"per-call model override {model!r} is not supported"
            )
            raise ValueError(msg)
        if not texts:
            return EmbeddingResult(vectors=[], n_tokens=0, cost_usd=0.0)

        rid = await self._budget.reserve(0.0)
        try:
            te = await self._ensure_loaded()
            vectors, n_tokens = await to_thread.run_sync(self._embed_sync, te, texts)
        finally:
            await self._budget.settle(rid, 0.0)
        return EmbeddingResult(vectors=vectors, n_tokens=n_tokens, cost_usd=0.0)

    @staticmethod
    def _embed_sync(
        te: object, texts: list[str]
    ) -> tuple[list[list[float]], int]:
        """Run fastembed inference + tokenizer count on a worker thread.

        Vectors are L2-normalized before return so cosine == dot in Qdrant.
        fastembed routes ``nomic-ai/nomic-embed-text-v1.5`` through
        ``PooledEmbedding`` (mean pooling without normalization), so we
        normalize here.
        """
        gen = te.embed(texts)  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownVariableType]
        vectors: list[list[float]] = []
        for v in gen:  # pyright: ignore[reportUnknownVariableType]
            arr = np.asarray(v, dtype=np.float32)
            norm = float(np.linalg.norm(arr))
            if norm > 0.0:
                arr = arr / norm
            vectors.append(arr.tolist())
        n_tokens = int(te.token_count(texts))  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]
        return vectors, n_tokens
```

Notes:
- `_te: object | None` keeps the `TextEmbedding` import out of the type signature so the module imports cheaply.
- `cache_dir=None` lets fastembed pick `~/.cache/fastembed`. Tests pass `cache_dir=tmp_path` to isolate state.
- Per-call `model` override raises rather than silently ignoring — fastembed loads one model per instance, and a mismatch is a programmer error. `OpenAIEmbeddingClient` does accept overrides via the `eff_model` path (`slopmortem/llm/openai_embeddings.py:76`); the contract divergence is intentional and covered by Task 4.
- `lazy_load=True` defers the ONNX session creation in `TextEmbedding.__init__` so construction is cheap; the session opens on first `embed()` / `token_count()` call. The download step still runs at construction (a no-op when the model is cached).
- Token count goes through fastembed's public `te.token_count(texts)` API rather than reaching into `te.model.tokenizer`, which is `None` until the ONNX session loads under `lazy_load=True`. `token_count` itself triggers the load when needed.
- `# pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownVariableType]` follows the established repo pattern (see `slopmortem/ingest.py:204`); a bare `# type: ignore` would either be flagged as unnecessary by basedpyright's strict config or fail to suppress strict-mode `reportAny` / `reportUnknown*` errors.

- [ ] **Step 1.5: Run the dim test and verify it passes**

Run: `uv run pytest tests/llm/test_fastembed_client.py::test_dim_matches_embed_dims_registry -v`
Expected: PASS.

- [ ] **Step 1.6: Add the empty-input short-circuit to `OpenAIEmbeddingClient`**

In `slopmortem/llm/openai_embeddings.py`, modify `embed` (currently at `:74`). Insert immediately after the `eff_model = model or self.model` line:

```python
if not texts:
    return EmbeddingResult(vectors=[], n_tokens=0, cost_usd=0.0)
```

This avoids reserving a non-zero ceiling and calling the SDK with empty `input` (the OpenAI SDK errors today). It mirrors the new fastembed contract and keeps the two providers identical on edges.

- [ ] **Step 1.7: Add a regression test for the OpenAI empty-input short-circuit**

Append to `tests/llm/test_embeddings.py`:

```python
async def test_openai_embed_empty_input_returns_empty_without_calling_sdk(fake_sdk):
    c = OpenAIEmbeddingClient(sdk=fake_sdk, budget=Budget(1.0), model="text-embedding-3-small")
    r = await c.embed([])
    assert r.vectors == []
    assert r.n_tokens == 0
    assert r.cost_usd == 0.0
    fake_sdk.embeddings.create.assert_not_called()
```

- [ ] **Step 1.8: Re-export the new client from `slopmortem/llm/__init__.py`**

Edit `slopmortem/llm/__init__.py`. Replace its single docstring line with:

```python
"""LLM and embedding clients, prompt rendering, and OpenRouter retry logic."""

from slopmortem.llm.fake_embeddings import FakeEmbeddingClient
from slopmortem.llm.fastembed_client import FastEmbedEmbeddingClient
from slopmortem.llm.openai_embeddings import EMBED_DIMS, OpenAIEmbeddingClient

__all__ = [
    "EMBED_DIMS",
    "FakeEmbeddingClient",
    "FastEmbedEmbeddingClient",
    "OpenAIEmbeddingClient",
]
```

- [ ] **Step 1.9: Verify `pyproject.toml` already declares `fastembed`**

Run: `grep '^  "fastembed' pyproject.toml`
Expected: a line like `"fastembed>=0.8",`. If absent, add it under `[project] dependencies`.

- [ ] **Step 1.10: Refresh the lock file**

Run: `uv lock`
Expected: `uv.lock` either unchanged or updated to a resolved fastembed pin. No new errors.

- [ ] **Step 1.11: Run the full embeddings test file**

Run: `uv run pytest tests/llm/test_embeddings.py tests/llm/test_fastembed_client.py -v`
Expected: all existing tests still pass; the new `test_openai_embed_empty_input_returns_empty_without_calling_sdk` and `test_dim_matches_embed_dims_registry` pass.

---

## Task 2: Config defaults flip + `embed_cache_dir` + `slopmortem.toml`

**Files:**
- Modify: `slopmortem/config.py:43-44` (flip defaults; add `embed_cache_dir`)
- Modify: `slopmortem.toml:18-19` (flip the matching TOML lines)

**Context for the implementer:**

- `Config.settings_customise_sources` (`slopmortem/config.py:74`) places the TOML source after env, so the TOML value wins at runtime. If only `config.py` defaults flip but `slopmortem.toml` still pins `"openai"`, the runtime default is `"openai"` and the change is a no-op.
- `embed_cache_dir` is `Path | None`. Pydantic-settings parses TOML strings into `Path` automatically; `None` is the type default and means "let fastembed pick".

- [ ] **Step 2.1: Write a failing test for the config default**

Append to `tests/llm/test_embeddings.py` (existing test file, has the imports already):

```python
def test_config_defaults_to_fastembed_with_nomic(tmp_path, monkeypatch):
    # Run with no slopmortem.toml or env present so we read pure code defaults.
    monkeypatch.chdir(tmp_path)
    from slopmortem.config import Config
    cfg = Config()
    assert cfg.embedding_provider == "fastembed"
    assert cfg.embed_model_id == "nomic-ai/nomic-embed-text-v1.5"
    assert cfg.embed_cache_dir is None
```

- [ ] **Step 2.2: Run the test and verify it fails**

Run: `uv run pytest tests/llm/test_embeddings.py::test_config_defaults_to_fastembed_with_nomic -v`
Expected: FAIL — `embedding_provider == "openai"` and `AttributeError` on `embed_cache_dir`.

- [ ] **Step 2.3: Edit `slopmortem/config.py` defaults**

Replace `slopmortem/config.py:43-44`:

```python
embedding_provider: str = "openai"
embed_model_id: str = "text-embedding-3-small"
```

with:

```python
embedding_provider: str = "fastembed"
embed_model_id: str = "nomic-ai/nomic-embed-text-v1.5"
embed_cache_dir: Path | None = None
```

`Path` is already imported at `slopmortem/config.py:5`.

- [ ] **Step 2.4: Run the config test and verify it passes**

Run: `uv run pytest tests/llm/test_embeddings.py::test_config_defaults_to_fastembed_with_nomic -v`
Expected: PASS.

- [ ] **Step 2.5: Flip `slopmortem.toml:18-19`**

Replace:

```toml
embedding_provider = "openai"
embed_model_id = "text-embedding-3-small"
```

with:

```toml
embedding_provider = "fastembed"
embed_model_id = "nomic-ai/nomic-embed-text-v1.5"
```

Do not add `embed_cache_dir` to the TOML — leave it absent so users default to `~/.cache/fastembed` without needing to know about the override.

- [ ] **Step 2.6: Verify the TOML override matches code**

Run: `uv run python -c "from slopmortem.config import load_config; c = load_config(); print(c.embedding_provider, c.embed_model_id, c.embed_cache_dir)"`
Expected output: `fastembed nomic-ai/nomic-embed-text-v1.5 None`.

- [ ] **Step 2.7: Confirm ingest tests still read from config rather than literals**

Run: `uv run pytest tests/test_ingest_dry_run.py tests/test_ingest_idempotency.py tests/test_ingest_orchestration.py -q`
Expected: all pass. They construct `FakeEmbeddingClient(model=cfg.embed_model_id)`, so the new default flows through automatically.

---

## Task 3: CLI `_make_embedder` factory + `embed-prefetch` subcommand

**Files:**
- Modify: `slopmortem/cli.py:55` (imports), `:362-366` (replace inline construction in `_build_deps`), `:443-447` (replace inline construction in `_build_ingest_deps`); add `_make_embedder` and the `embed-prefetch` typer command.

**Context for the implementer:**

- The two inline constructions are identical except for which budget they share. After the refactor, both call `embedder = _make_embedder(config, budget)`.
- `_build_deps` is shared by `query` (`slopmortem/cli.py:297`) and `replay` (`:495`). `_build_ingest_deps` (`:420`) is used by `ingest`. Both must keep working.
- `embed-prefetch` is a new typer command registered on the existing `app = typer.Typer(...)` at `slopmortem/cli.py:76`. It calls `load_config()`, builds an embedder with a throwaway `Budget`, calls `_ensure_loaded()`, and exits 0; on failure it prints to stderr and exits 1.
- For the `OPENAI_API_KEY` env var: today both `_build_deps` and `_build_ingest_deps` unconditionally read `os.environ["OPENAI_API_KEY"]`. After the refactor, when `embedding_provider == "fastembed"` no key should be required. Move the OpenAI-only env read inside the `"openai"` branch of the factory.

- [ ] **Step 3.1: Write a failing test for the factory's provider dispatch**

Create `tests/llm/test_embedder_factory.py`:

```python
from __future__ import annotations

import pytest

from slopmortem.budget import Budget
from slopmortem.cli import _make_embedder
from slopmortem.config import Config
from slopmortem.llm.fastembed_client import FastEmbedEmbeddingClient
from slopmortem.llm.openai_embeddings import OpenAIEmbeddingClient


def test_factory_returns_fastembed_for_fastembed_provider():
    cfg = Config(embedding_provider="fastembed", embed_model_id="nomic-ai/nomic-embed-text-v1.5")
    e = _make_embedder(cfg, Budget(0.0))
    assert isinstance(e, FastEmbedEmbeddingClient)
    assert e.model == "nomic-ai/nomic-embed-text-v1.5"


def test_factory_returns_openai_for_openai_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = Config(embedding_provider="openai", embed_model_id="text-embedding-3-small")
    e = _make_embedder(cfg, Budget(0.0))
    assert isinstance(e, OpenAIEmbeddingClient)
    assert e.model == "text-embedding-3-small"


def test_factory_raises_on_unknown_provider():
    cfg = Config(embedding_provider="ollama", embed_model_id="text-embedding-3-small")
    with pytest.raises(ValueError, match="ollama"):
        _make_embedder(cfg, Budget(0.0))
```

- [ ] **Step 3.2: Run the factory tests and verify they fail**

Run: `uv run pytest tests/llm/test_embedder_factory.py -v`
Expected: FAIL with `ImportError: cannot import name '_make_embedder' from slopmortem.cli`.

- [ ] **Step 3.3: Add the import for `FastEmbedEmbeddingClient` to `slopmortem/cli.py`**

Replace `slopmortem/cli.py:55`:

```python
from slopmortem.llm.openai_embeddings import OpenAIEmbeddingClient
```

with:

```python
from slopmortem.llm.fastembed_client import FastEmbedEmbeddingClient
from slopmortem.llm.openai_embeddings import OpenAIEmbeddingClient
```

- [ ] **Step 3.4: Add `_make_embedder` to `slopmortem/cli.py`**

Insert the following helper immediately above `_build_deps` (currently at `slopmortem/cli.py:336`):

```python
def _make_embedder(config: Config, budget: Budget) -> EmbeddingClient:
    """Construct the configured embedder; branch on ``config.embedding_provider``.

    Unknown provider names raise ``ValueError`` listing the supported values so
    a typo in ``slopmortem.toml`` fails loud at startup rather than mid-pipeline.
    """
    provider = config.embedding_provider
    if provider == "fastembed":
        return FastEmbedEmbeddingClient(
            model=config.embed_model_id,
            budget=budget,
            cache_dir=config.embed_cache_dir,
        )
    if provider == "openai":
        openai_sdk = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
        return OpenAIEmbeddingClient(
            sdk=openai_sdk,
            budget=budget,
            model=config.embed_model_id,
        )
    valid = ("fastembed", "openai")
    msg = f"unknown embedding_provider {provider!r}; valid choices: {valid}"
    raise ValueError(msg)
```

`Config` is already imported via `TYPE_CHECKING` at `slopmortem/cli.py:65`; the `EmbeddingClient` Protocol is imported at `:72`. Move both out of `TYPE_CHECKING` only if pyright complains — the `_make_embedder` body uses `Budget` (already imported) and class names that are runtime references already.

Actually `Config` and `EmbeddingClient` are both inside `TYPE_CHECKING`. They are used here only as type annotations on the function signature, so they stay inside `TYPE_CHECKING` and the annotations work because of `from __future__ import annotations` at the top of the file (`slopmortem/cli.py:28`).

- [ ] **Step 3.5: Replace inline construction in `_build_deps`**

In `slopmortem/cli.py:361-366`, replace:

```python
    openai_sdk = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    embedder = OpenAIEmbeddingClient(
        sdk=openai_sdk,
        budget=budget,
        model=config.embed_model_id,
    )
```

with:

```python
    embedder = _make_embedder(config, budget)
```

- [ ] **Step 3.6: Replace inline construction in `_build_ingest_deps`**

In `slopmortem/cli.py:442-447`, replace:

```python
    openai_sdk = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    embedder = OpenAIEmbeddingClient(
        sdk=openai_sdk,
        budget=budget,
        model=config.embed_model_id,
    )
```

with:

```python
    embedder = _make_embedder(config, budget)
```

- [ ] **Step 3.7: Run the factory tests and verify they pass**

Run: `uv run pytest tests/llm/test_embedder_factory.py -v`
Expected: all three tests PASS.

- [ ] **Step 3.8: Add the `embed-prefetch` subcommand**

Append to `slopmortem/cli.py` (just above `if __name__ == "__main__":` at `:519`):

```python
# ---------------------------------------------------------------------------
# embed-prefetch
# ---------------------------------------------------------------------------


@app.command("embed-prefetch")
def embed_prefetch_cmd() -> None:
    """Warm the configured embedder's model cache (useful for CI / first-run)."""
    anyio.run(_embed_prefetch)


async def _embed_prefetch() -> None:
    config = load_config()
    budget = Budget(cap_usd=0.0)
    embedder = _make_embedder(config, budget)
    if not isinstance(embedder, FastEmbedEmbeddingClient):
        typer.echo(
            f"slopmortem: provider {config.embedding_provider!r} has no local cache to prefetch",
            err=True,
        )
        return
    try:
        await embedder._ensure_loaded()  # noqa: SLF001 — CLI surface owns the cache-warm trigger
    except Exception as exc:  # noqa: BLE001 — surface any load failure as exit 1
        typer.echo(f"slopmortem: embed-prefetch failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"slopmortem: prefetched {config.embed_model_id} into the fastembed cache")
```

- [ ] **Step 3.9: Smoke-test the new subcommand wires up**

Run: `uv run slopmortem embed-prefetch --help`
Expected: typer prints help text including `embed-prefetch` description; exit code 0.

- [ ] **Step 3.10: Run the full test suite to confirm no regressions in CLI wiring**

Run: `uv run pytest tests/ -q -x --ignore=tests/llm/test_fastembed_client.py`
Expected: all pre-existing tests pass. (We exclude the slow fastembed file because its model-loading tests land in Task 4 and need a live model download.)

---

## Task 4: Tests for `FastEmbedEmbeddingClient` and the factory

**Files:**
- Modify: `tests/llm/test_fastembed_client.py` (started in Task 1; expand)
- Modify: `pyproject.toml` `[tool.pytest.ini_options].markers` (add `slow` marker)

**Context for the implementer:**

- The test file already has `test_dim_matches_embed_dims_registry` (added in Task 1.1). This task adds the rest.
- Tests that load the real ONNX model are marked `@pytest.mark.slow`. CI's fast lane runs `pytest -m "not slow"`; the heavy model-load tests run in a separate lane.
- For the empty-input test, we assert that `_te is None` after the call — proving the model never loaded.
- For the unknown-model test, we don't need a live model — the `__init__` raises before any disk work.
- `tests/llm/test_embedder_factory.py` was created in Task 3 and is complete. No changes here.

- [ ] **Step 4.1: Add the `slow` marker to `pyproject.toml`**

Edit `pyproject.toml` `[tool.pytest.ini_options].markers`. Replace:

```toml
markers = [
  "requires_qdrant: integration test that requires a live Qdrant on localhost:6333",
]
```

with:

```toml
markers = [
  "requires_qdrant: integration test that requires a live Qdrant on localhost:6333",
  "slow: test loads heavy assets (e.g., a ~550MB ONNX model). Excluded with -m 'not slow'.",
]
```

- [ ] **Step 4.2: Add the empty-input test (no model load)**

Append to `tests/llm/test_fastembed_client.py`:

```python
async def test_embed_empty_returns_empty_without_loading_model():
    c = FastEmbedEmbeddingClient(model="nomic-ai/nomic-embed-text-v1.5", budget=Budget(0.0))
    r = await c.embed([])
    assert r.vectors == []
    assert r.n_tokens == 0
    assert r.cost_usd == 0.0
    # Model must not have been materialized.
    assert c._te is None  # noqa: SLF001 — explicit lazy-load contract assertion
```

- [ ] **Step 4.3: Add the unknown-model test (no model load)**

Append to `tests/llm/test_fastembed_client.py`:

```python
def test_unknown_model_raises_with_embed_dims_in_message():
    with pytest.raises(ValueError, match="EMBED_DIMS"):
        FastEmbedEmbeddingClient(model="nomic-embed-text-v999", budget=Budget(0.0))
```

`pytest` is already imported at the top of the file from Step 1.1 — do not add another import.

- [ ] **Step 4.4: Add the per-call model-override rejection test**

Append to `tests/llm/test_fastembed_client.py`:

```python
async def test_per_call_model_override_rejected():
    c = FastEmbedEmbeddingClient(model="nomic-ai/nomic-embed-text-v1.5", budget=Budget(0.0))
    with pytest.raises(ValueError, match="not supported"):
        await c.embed(["x"], model="text-embedding-3-small")
```

- [ ] **Step 4.5: Add the slow real-model integration test**

Append to `tests/llm/test_fastembed_client.py`:

```python
@pytest.mark.slow
async def test_embed_returns_normalized_vectors_with_correct_dim(tmp_path):
    import math

    c = FastEmbedEmbeddingClient(
        model="nomic-ai/nomic-embed-text-v1.5",
        budget=Budget(0.0),
        cache_dir=tmp_path,
    )
    r = await c.embed(["hello", "world"])
    assert len(r.vectors) == 2
    assert all(len(v) == 768 for v in r.vectors)
    # Vectors must be L2-normalized so cosine == dot in Qdrant.
    for v in r.vectors:
        norm = math.sqrt(sum(x * x for x in v))
        assert norm == pytest.approx(1.0, rel=1e-3)
    assert r.cost_usd == 0.0
    assert r.n_tokens > 0
```

- [ ] **Step 4.6: Run the fast-lane tests (should pass without downloading the model)**

Run: `uv run pytest tests/llm/test_fastembed_client.py -v -m "not slow"`
Expected: `test_dim_matches_embed_dims_registry`, `test_embed_empty_returns_empty_without_loading_model`, `test_unknown_model_raises_with_embed_dims_in_message`, `test_per_call_model_override_rejected` all PASS. The slow test is collected and skipped.

- [ ] **Step 4.7: Run the slow lane to confirm the integration test works on a real model**

Run: `uv run pytest tests/llm/test_fastembed_client.py::test_embed_returns_normalized_vectors_with_correct_dim -v -m slow`
Expected: PASS (first run downloads ~550MB into `tmp_path`, subsequent runs use the same fixture path and re-download). If your environment has no network, mark this step DEFERRED in the PR description and run it locally before merge.

- [ ] **Step 4.8: Final whole-suite check**

Run: `uv run pytest tests/ -m "not slow" -q`
Expected: all tests pass; no regressions in ingest, retrieve, evals, or CLI tests.

- [ ] **Step 4.9: Lint and typecheck**

Run: `uv run ruff check . && uv run ruff format --check . && uv run basedpyright`
Expected: clean.

---

## Self-Review Findings

**Spec coverage (re-checked against `docs/specs/2026-04-29-onnx-embedding-design.md`):**

- Goal: `embedding_provider="fastembed"` default ✓ (Task 2); `nomic-ai/nomic-embed-text-v1.5` ✓ (Task 1.3, Task 2); ingest/retrieve unchanged ✓ (Task 1 Protocol-conformant); `embed-prefetch` CLI ✓ (Task 3.8).
- Components — `FastEmbedEmbeddingClient` ✓ (Task 1.4 with full body, including L2-normalization for cosine==dot in Qdrant); `EMBED_DIMS` extension + `OPENAI_EMBED_MODELS` split ✓ (Task 1.3); empty-input symmetry on `OpenAIEmbeddingClient` ✓ (Task 1.6); `slopmortem/llm/__init__.py` re-export ✓ (Task 1.8); `slopmortem.toml` flip ✓ (Task 2.5); `_make_embedder` replacing both call sites ✓ (Task 3.5, 3.6).
- Error handling — model-load failure wrapping with `embed-prefetch` hint ✓ (Task 1.4 in `_load_sync`); unknown-model raise ✓ (Task 1.4 + Task 4.3); empty-input short-circuit ✓ (Task 1.6 + Task 4.2); no transient retry on local inference ✓ (intentionally absent from Task 1 implementation).
- Testing — `tests/llm/test_fastembed_client.py` covers dim, normalization, empty-without-load, unknown model, cost/tokens ✓ (Task 4.2–4.5); `tests/llm/test_embedder_factory.py` covers all three branches ✓ (Task 3.1); existing tests unchanged ✓ (Task 2.7 verification step).
- Out-of-scope items confirmed absent: no per-model collection logic, no auto-rebuild, no third provider, no eager load.

**Placeholder scan:** zero `TBD`/`later`/`appropriate`/`similar to Task N`. All code blocks contain runnable code; all commands have explicit expected output.

**Type consistency:** `_ensure_loaded()` referenced in Task 1.4 implementation, Task 3.8 CLI, and Task 1 spec context — same name everywhere. `_make_embedder(config, budget)` signature identical at definition (Task 3.4) and both call sites (Task 3.5, 3.6). `EMBED_DIMS["nomic-ai/nomic-embed-text-v1.5"] = 768` consistent across Task 1.3 (definition), Task 1.1 (test), Task 4.5 (assertion).

One nuance worth flagging for the executor: Task 1 creates `tests/llm/test_fastembed_client.py` with a single test, and Task 4 expands the same file. The executor should not delete and re-create — append only.
