# Encapsulation Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or `/team-feature` to implement this plan task-by-task, per the Execution Strategy below. Steps use checkbox (`- [ ]`) syntax — these are **persistent durable state**, not visual decoration. The executor edits the plan file in place: `- [ ]` → `- [x]` the instant a step verifies, before moving on. On resume (new session, crash, takeover), the executor scans existing `- [x]` marks and skips them — these steps are NOT redone. TodoWrite mirrors this state in-session; the plan file is the source of truth across sessions.

**Goal:** Turn the aspirational `__init__.py` façades into load-bearing boundaries enforced by import-linter, split `ingest.py` and `cli.py` into focused sub-packages, and finish with an `_internal/` directory convention.

**Architecture:** Five PRs, each independently shippable. PR 0 wires tooling (import-linter + smoke recipe). PR 1 fixes façades and locks them with import-linter. PR 2 splits `ingest.py` (1149 LOC) into a package with named files for warm-cache, fan-out, journal-writes, and slop-gate. PR 3 does the same for `cli.py` (850 LOC). PR 4 moves underscore-prefixed files into `_internal/` directories.

**Tech Stack:** Python 3.13+, anyio, Pydantic v2, basedpyright (strict), ruff, import-linter, just, pytest with cassettes.

## Execution Strategy

**Subagents.** Tasks dispatched to fresh `python-development:python-pro` agents one at a time. The user's standing preference is sequential execution (no parallel batching), so the dependency graph below chains all tasks linearly within each PR even where independence would technically allow parallelism (e.g., T1.1–T1.4 expand four different `__init__.py` files and could run in parallel, but we run them sequentially per user preference).

Reason: the work is mechanical and low-coordination, but each task's correctness depends on a clean checkpoint before the next starts. Sequential dispatch keeps `main` continuously green and matches the user's preferred workflow.

## Task Dependency Graph

- T0.1 [AFK]: depends on `none` → first batch
- T0.2 [AFK]: depends on `T0.1` → sequential
- T0.3 [AFK]: depends on `T0.2` → sequential
- T1.0 [AFK]: depends on `T0.3` → sequential (verification-only)
- T1.1 [AFK]: depends on `T1.0` → sequential
- T1.2 [AFK]: depends on `T1.1` → sequential
- T1.3 [AFK]: depends on `T1.2` → sequential
- T1.4 [AFK]: depends on `T1.3` → sequential
- T1.5 [AFK]: depends on `T1.4` → sequential
- T1.6 [AFK]: depends on `T1.5` → sequential
- T1.7 [AFK]: depends on `T1.6` → sequential
- T2.1 [HITL]: depends on `T1.7` → gated PR boundary, regenerate cassettes on `main` first
- T2.2 [AFK]: depends on `T2.1` → sequential
- T2.3 [AFK]: depends on `T2.2` → sequential
- T2.4 [AFK]: depends on `T2.3` → sequential (new tests authored)
- T2.5 [AFK]: depends on `T2.4` → sequential (new tests authored)
- T2.6 [AFK]: depends on `T2.5` → sequential
- T2.7 [AFK]: depends on `T2.6` → sequential
- T3.1 [AFK]: depends on `T2.7` → sequential
- T3.2 [AFK]: depends on `T3.1` → sequential
- T3.3 [AFK]: depends on `T3.2` → sequential
- T3.4 [AFK]: depends on `T3.3` → sequential
- T3.5 [AFK]: depends on `T3.4` → sequential
- T3.6 [AFK]: depends on `T3.5` → sequential
- T4.1 [HITL]: depends on `T3.6` → gated PR boundary, reassess whether PR 4 is still wanted
- T4.2 [AFK]: depends on `T4.1` → sequential
- T4.3 [AFK]: depends on `T4.2` → sequential
- T4.4 [AFK]: depends on `T4.3` → sequential

All 28 tasks run sequentially per user preference. PR boundaries (T2.1, T4.1) are HITL gates: regenerate cassettes / reassess scope before proceeding.

## Agent Assignments

- T0.1 — Add import-linter dep             → python-development:python-pro
- T0.2 — Wire lint into just + CI          → python-development:python-pro
- T0.3 — just smoke recipe                 → python-development:python-pro
- T1.0 — Verify prod←evals leak closed     → python-development:python-pro
- T1.1 — Expand corpus/__init__            → python-development:python-pro
- T1.2 — Expand llm/__init__               → python-development:python-pro
- T1.3 — stages/__init__ exports           → python-development:python-pro
- T1.4 — corpus/sources/__init__ exports   → python-development:python-pro
- T1.5 — Sweep imports to façades          → python-development:python-pro
- T1.6 — .importlinter contracts (PR 1)    → python-development:python-pro
- T1.7 — Rename internals with _           → python-development:python-pro
- T2.1 — git mv ingest.py to package       → python-development:python-pro
- T2.2 — Extract _warm_cache               → python-development:python-pro
- T2.3 — Extract _fan_out                  → python-development:python-pro
- T2.4 — Extract _journal_writes + tests   → python-development:python-pro
- T2.5 — Extract _slop_gate + test         → python-development:python-pro
- T2.6 — Trim ingest/__init__ __all__      → python-development:python-pro
- T2.7 — .importlinter for ingest          → python-development:python-pro
- T3.1 — cli.py to cli/ package            → python-development:python-pro
- T3.2 — Extract subcommand files          → python-development:python-pro
- T3.3 — cli/_common.py                    → python-development:python-pro
- T3.4 — Verify entrypoint + --help        → python-development:python-pro
- T3.5 — cli/__init__ __all__              → python-development:python-pro
- T3.6 — .importlinter for cli             → python-development:python-pro
- T4.1 — Move to _internal/                → python-development:python-pro
- T4.2 — Update intra-package imports      → python-development:python-pro
- T4.3 — Tighten importlinter              → python-development:python-pro
- T4.4 — Update CLAUDE.md layout           → python-development:python-pro
- Polish: post-implementation-polish       → general-purpose

**Subagent brief boilerplate (every task):** "Do not stage, do not commit, stay strictly within the CREATE/MODIFY file list for this task. Parent session owns commit authorship and may bundle multiple completed tasks into one commit at PR boundaries."

---

## Source design spec

This plan implements `docs/specs/2026-05-01-encapsulation-refactor-design.md`. Read the spec for problem framing, alternatives considered, and risks. The plan below decomposes each task in the spec's work breakdown into bite-sized steps. Where the spec gives line numbers (e.g. `ingest.py:758`), the plan trusts them — verify on the executing agent's first read of the file and report drift if found.

---

## PR 0 — Tooling prep

### Task T0.1: Add import-linter dependency

**Files:**
- Modify: `pyproject.toml`
- Create: `.importlinter`

- [x] **Step 1: Add `import-linter` to dev dependency group**

Edit `pyproject.toml` `[dependency-groups]` `dev = [...]`. Insert `"import-linter>=2.4",` in the alphabetical position (after `basedpyright`).

- [x] **Step 2: Create `.importlinter` with bare-minimum config**

Write `.importlinter`:

```ini
[importlinter]
root_package = slopmortem
```

No contracts yet — they land in T1.6. `lint-imports` errors on a missing `root_package`, so this stub is required even before contracts exist.

- [x] **Step 3: Sync deps and verify the binary is on PATH**

Run: `uv sync && uv run lint-imports --help`
Expected: help text printed, exit 0.

- [x] **Step 4: Verify lint-imports runs against the empty contract list**

Run: `uv run lint-imports`
Expected: `Skipped: 0 / Kept: 0 / Broken: 0` or equivalent "no contracts" success message, exit 0.

### Task T0.2: Wire lint-imports into `just lint` and CI

**Files:**
- Modify: `justfile`
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add a `lint-imports` recipe to the justfile**

Edit `justfile`. The current `lint` recipe is at lines 46–48:

```makefile
lint:
    uv run ruff check .
    uv run ruff format --check .
```

The CI `lint` job uses `astral-sh/ruff-action@v4.0.0` directly — it has no `uv sync` step, so adding `lint-imports` to the `lint` recipe **and** the CI `lint` job would make CI fail. Strategy: add `lint-imports` to `just lint` (developers run it locally) AND add it to the `typecheck` CI job (which already runs `uv sync`).

Replace the `lint` recipe with:

```makefile
lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run lint-imports
```

- [ ] **Step 2: Wire lint-imports into the CI typecheck job**

Edit `.github/workflows/ci.yml`. Append a step to the `typecheck` job (after `uv run basedpyright`):

```yaml
      - run: uv run lint-imports
```

- [ ] **Step 3: Run `just lint` locally and verify success**

Run: `just lint`
Expected: ruff check, format check, and lint-imports all pass, exit 0.

### Task T0.3: Add `just smoke` recipe

**Files:**
- Modify: `justfile`

- [ ] **Step 1: Add the `smoke` recipe**

Append to `justfile` (after the `eval-record-corpus` recipe is the natural spot):

```makefile
# Fast import-time + cassette smoke. Catches typer registration regressions
# and LLM-pipeline drift without hitting any live API. Used by every
# refactor checkpoint in docs/plans/2026-05-02-encapsulation-refactor.md.
smoke:
    uv run slopmortem --help
    uv run slopmortem ingest --help
    uv run slopmortem query --help
    uv run slopmortem replay --help
    uv run slopmortem embed-prefetch --help
    just eval
```

- [ ] **Step 2: Run the smoke recipe and capture output**

Run: `just smoke`
Expected: each `--help` prints typer's usage block, then `just eval` runs the cassette suite to completion with `Pass`/`Fail` baselines stable. Exit 0.

- [ ] **Step 3: Verify the recipe is listed**

Run: `just --list | grep smoke`
Expected: `    smoke   Fast import-time + cassette smoke...`

### PR 0 checkpoint

- [ ] **All three tasks committed; run end-to-end gate**

Run: `just test && just lint && just typecheck && just smoke`
Expected: all green. This is the baseline `main` will branch off for PR 1.

---

## PR 1 — Façade hygiene (Approach A)

Order is load-bearing: expand façades (additive, can't break callers) → migrate callers → lock the door with import-linter → rename last.

**Re-export idiom (applies to T1.1–T1.4).** Use `from … import X as X` to satisfy basedpyright strict-mode `reportImplicitReexport`. Convert any existing bare imports in the touched `__init__.py` files to the explicit form while you're there.

### Task T1.0: Verify the prod←evals leak is closed

**Files:**
- Verify only: `slopmortem/llm/fake_embeddings.py`, `slopmortem/llm/cassettes.py`, all of `slopmortem/`

- [ ] **Step 1: Grep for any prod-side import from `slopmortem.evals`**

Run: `grep -rnE "from slopmortem\.evals" slopmortem/ | grep -vE "^slopmortem/evals/"`
Expected: empty output (no prod module imports from evals).

- [ ] **Step 2: Confirm `NoCannedEmbeddingError` lives under `slopmortem.llm`**

Run: `grep -rn "class NoCannedEmbeddingError" slopmortem/`
Expected: `slopmortem/llm/cassettes.py:...`

- [ ] **Step 3: Confirm `fake_embeddings.py` imports it from `slopmortem.llm.cassettes`**

Run: `grep -n "NoCannedEmbeddingError" slopmortem/llm/fake_embeddings.py`
Expected: an import line referencing `slopmortem.llm.cassettes`, no reference to `slopmortem.evals`.

- [ ] **Step 4: Stop and report drift if any check fails**

If any of the three steps above fail, the leak T1.0 thought was closed has reopened. Stop. Report to operator with the failing grep output. Do NOT proceed to T1.1.

If all three pass, this task is verification-only — no edit, no commit.

### Task T1.1: Expand `corpus/__init__.py`

**Files:**
- Modify: `slopmortem/corpus/__init__.py`
- Modify: `slopmortem/corpus/tools_impl.py` (add public `set_query_corpus` wrapper)

- [ ] **Step 1: Enumerate outside callers of `slopmortem.corpus.*`**

Run: `grep -rnE "^from slopmortem\.corpus\." slopmortem/ tests/ | grep -v "^slopmortem/corpus/"`

Capture the output as the verification snapshot. Any submodule with at least one outside caller is a candidate for re-export. Submodules with no outside callers stay private.

- [ ] **Step 2: Add a public `set_query_corpus` wrapper to `tools_impl.py`**

Read `slopmortem/corpus/tools_impl.py`. The existing private function is `def _set_corpus(c: Corpus) -> None:` at `tools_impl.py:104`. `Corpus` is imported from `slopmortem.corpus.store` under `TYPE_CHECKING`. Append a public wrapper that mirrors the same typed signature — do NOT widen to `object`, basedpyright strict (`reportAny="error"`) will reject it:

```python
def set_query_corpus(c: Corpus) -> None:
    """Bind the corpus the query-side LLM tools should call into.

    Public re-export of the module-private `_set_corpus`. Required so
    callers (`cli.py`, `evals/runner.py`, `evals/recording_helper.py`)
    can avoid reaching past the `corpus` package façade.
    """
    _set_corpus(c)
```

If the underlying signature changes after this plan is written (different param name or type), mirror it verbatim. Keep the parameter name aligned so positional and keyword callers both work.

- [ ] **Step 3: Expand `corpus/__init__.py` with the verified re-export list**

Edit `slopmortem/corpus/__init__.py`. Use the explicit `from … import X as X` form so basedpyright is satisfied. Add re-exports for: `entity_resolution.resolve_entity`, `paths.safe_path`, `extract.extract_clean`, `reclassify.reclassify_quarantined`, `merge_text.Section`, `merge_text.combined_hash`, `merge_text.combined_text`, `tools_impl.set_query_corpus`, and `tools_impl.TAVILY_EXTRACT_URL` if and only if step 1's grep showed it has an outside caller. Convert existing bare imports to the `as X` form too.

- [ ] **Step 4: Persist the verification result as a header comment**

Prepend a comment block to `slopmortem/corpus/__init__.py`:

```python
# T1.1 verification (date YYYY-MM-DD): submodule audit results
#   <module> = <internal-only | external (N sites) | TYPE_CHECKING-only (N sites)>
# T1.7 reads this to decide which modules are safe to underscore.
```

Fill in actual results from step 1 for: `store`, `summarize`, `alias_graph`, `embed_sparse`, `merge_text`, `tools_impl`. Use today's date.

- [ ] **Step 5: Run typecheck and smoke**

Run: `just typecheck && just smoke`
Expected: green. `lint-imports` still passes (no contracts yet).

### Task T1.2: Expand `llm/__init__.py`

**Files:**
- Modify: `slopmortem/llm/__init__.py`

- [ ] **Step 1: Enumerate outside callers of `slopmortem.llm.*`**

Run: `grep -rnE "^from slopmortem\.llm\." slopmortem/ tests/ | grep -v "^slopmortem/llm/"`

The output shows which submodule symbols need to be re-exported. Take the snapshot.

- [ ] **Step 2: Read the current init**

Read `slopmortem/llm/__init__.py`. The current re-exports (verified): `EMBED_DIMS`, `OpenAIEmbeddingClient`, `FakeEmbeddingClient`, `FastEmbedEmbeddingClient`. Do not duplicate these.

- [ ] **Step 3: Expand with the new re-exports**

Replace the contents with the explicit-as form, adding:
- `OpenRouterClient` (from `openrouter`)
- `make_embedder` (from `embedding_factory`)
- `CompletionResult` (from `client`)
- `EmbeddingResult` (from `embedding_client`)
- `gather_with_limit`, `is_transient_http` (from `openrouter`)
- relevant `tools` exports (verify via step 1's grep)
- `NoCannedEmbeddingError` (from `cassettes`) — defined at `slopmortem/llm/cassettes.py:67`
- `NoCannedResponseError`, `FakeLLMClient`, `FakeResponse` (from `fake`) — `NoCannedResponseError` is at `slopmortem/llm/fake.py:15`, NOT in `cassettes.py`. Do not put it in the `cassettes` import block.

Final shape:

```python
"""LLM and embedding clients, prompt rendering, and OpenRouter retry logic."""

from __future__ import annotations

from slopmortem.llm.cassettes import (
    NoCannedEmbeddingError as NoCannedEmbeddingError,
    # add other outside-imported names from cassettes.py here
    # (NOT NoCannedResponseError — that lives in fake.py, see below)
)
from slopmortem.llm.client import CompletionResult as CompletionResult
from slopmortem.llm.embedding_client import EmbeddingResult as EmbeddingResult
from slopmortem.llm.embedding_factory import make_embedder as make_embedder
from slopmortem.llm.fake import (
    FakeLLMClient as FakeLLMClient,
    FakeResponse as FakeResponse,
    NoCannedResponseError as NoCannedResponseError,
)
from slopmortem.llm.fake_embeddings import FakeEmbeddingClient as FakeEmbeddingClient
from slopmortem.llm.fastembed_client import FastEmbedEmbeddingClient as FastEmbedEmbeddingClient
from slopmortem.llm.openai_embeddings import (
    EMBED_DIMS as EMBED_DIMS,
    OpenAIEmbeddingClient as OpenAIEmbeddingClient,
)
from slopmortem.llm.openrouter import (
    OpenRouterClient as OpenRouterClient,
    gather_with_limit as gather_with_limit,
    is_transient_http as is_transient_http,
)

__all__ = [
    "CompletionResult",
    "EMBED_DIMS",
    "EmbeddingResult",
    "FakeEmbeddingClient",
    "FakeLLMClient",
    "FakeResponse",
    "FastEmbedEmbeddingClient",
    "NoCannedEmbeddingError",
    "NoCannedResponseError",
    "OpenAIEmbeddingClient",
    "OpenRouterClient",
    "gather_with_limit",
    "is_transient_http",
    "make_embedder",
]
```

If step 1's grep shows additional symbols (e.g., from `tools.py` or other cassettes-module helpers), append them here.

- [ ] **Step 4: Run typecheck and smoke**

Run: `just typecheck && just smoke`
Expected: green.

### Task T1.3: Populate `stages/__init__.py`

**Files:**
- Modify: `slopmortem/stages/__init__.py`

- [ ] **Step 1: Enumerate outside callers**

Run: `grep -rnE "^from slopmortem\.stages\." slopmortem/ tests/ | grep -v "^slopmortem/stages/"`

`stages/__init__.py` is currently empty (only a module docstring + `from __future__ import annotations`) — there is nothing to "confirm" against. The grep output is the source of truth for what step 2 must export. Cross-check against the spec's expected list: `extract_facets`, `retrieve`, `llm_rerank`, `synthesize_all`, `consolidate_risks`, plus `synthesize` and `synthesize_prompt_kwargs` (used by `tests/stages/test_synthesize.py`, `tests/test_observe_redaction.py`, `tests/test_pipeline_e2e.py`). If the grep finds names not in this list, add them; if the grep is missing a name from this list, that name has no current outside caller and can be omitted.

- [ ] **Step 2: Replace the empty init with explicit re-exports**

Edit `slopmortem/stages/__init__.py`:

```python
"""Pipeline stages: facet_extract, retrieve, llm_rerank, synthesize, consolidate_risks."""

from __future__ import annotations

from slopmortem.stages.consolidate_risks import consolidate_risks as consolidate_risks
from slopmortem.stages.facet_extract import extract_facets as extract_facets
from slopmortem.stages.llm_rerank import llm_rerank as llm_rerank
from slopmortem.stages.retrieve import retrieve as retrieve
from slopmortem.stages.synthesize import (
    synthesize as synthesize,
    synthesize_all as synthesize_all,
    synthesize_prompt_kwargs as synthesize_prompt_kwargs,
)

__all__ = [
    "consolidate_risks",
    "extract_facets",
    "llm_rerank",
    "retrieve",
    "synthesize",
    "synthesize_all",
    "synthesize_prompt_kwargs",
]
```

- [ ] **Step 3: Run typecheck and smoke**

Run: `just typecheck && just smoke`
Expected: green.

### Task T1.4: Expand `corpus/sources/__init__.py`

**Files:**
- Modify: `slopmortem/corpus/sources/__init__.py`

- [ ] **Step 1: List the 5 adapter classes**

`corpus/sources/__init__.py` currently only re-exports the protocol bases (`Enricher`, `Source` from `base`). The 5 adapter classes live in their respective sibling files but are not yet re-exported — step 2 adds them.

Run: `grep -rnE "^class \w+(Source|Enricher)" slopmortem/corpus/sources/`
Expected (per spec): `CrunchbaseCsvSource`, `CuratedSource`, `HNAlgoliaSource`, `TavilyEnricher`, `WaybackEnricher`. Confirm the count is 5 and capture the file each lives in.

- [ ] **Step 2: Replace the init with explicit re-exports**

Edit `slopmortem/corpus/sources/__init__.py`:

```python
"""Source adapters and enrichers that produce ``RawEntry`` for ingest."""

from __future__ import annotations

from slopmortem.corpus.sources.base import Enricher as Enricher, Source as Source
from slopmortem.corpus.sources.crunchbase_csv import CrunchbaseCsvSource as CrunchbaseCsvSource
from slopmortem.corpus.sources.curated import CuratedSource as CuratedSource
from slopmortem.corpus.sources.hn_algolia import HNAlgoliaSource as HNAlgoliaSource
from slopmortem.corpus.sources.tavily import TavilyEnricher as TavilyEnricher
from slopmortem.corpus.sources.wayback import WaybackEnricher as WaybackEnricher

__all__ = [
    "CrunchbaseCsvSource",
    "CuratedSource",
    "Enricher",
    "HNAlgoliaSource",
    "Source",
    "TavilyEnricher",
    "WaybackEnricher",
]
```

- [ ] **Step 3: Run typecheck and smoke**

Run: `just typecheck && just smoke`
Expected: green.

### Task T1.5: Sweep all in-tree imports to use the façades

**Files:**
- Grep-derived list (~30 files in `slopmortem/` and `tests/`); fix the file list at step 1.

- [ ] **Step 1: Generate the file list**

Run:

```bash
grep -lrnE "(^|[[:space:]])from slopmortem\.(corpus|llm|stages|corpus\.sources)\." slopmortem/ tests/ \
    | grep -vE "^slopmortem/(corpus|llm|stages)/__init__\.py" \
    | sort -u
```

The grep output is the CREATE/MODIFY boundary for this task. Do not touch files outside it. Save the list to a scratch file or capture it in the PR description.

- [ ] **Step 2: Rewrite imports file by file**

For each file in the list, rewrite imports of submodules to imports from the package façade. Examples:

| Before | After |
|---|---|
| `from slopmortem.corpus.paths import safe_path` | `from slopmortem.corpus import safe_path` |
| `from slopmortem.corpus.extract import extract_clean` | `from slopmortem.corpus import extract_clean` |
| `from slopmortem.corpus.merge_text import Section, combined_hash, combined_text` | `from slopmortem.corpus import Section, combined_hash, combined_text` |
| `from slopmortem.llm.openrouter import OpenRouterClient` | `from slopmortem.llm import OpenRouterClient` |
| `from slopmortem.llm.embedding_factory import make_embedder` | `from slopmortem.llm import make_embedder` |
| `from slopmortem.corpus.tools_impl import _set_corpus` | `from slopmortem.corpus import set_query_corpus` (and rename the call site too) |
| `from slopmortem.corpus.sources.curated import CuratedSource` | `from slopmortem.corpus.sources import CuratedSource` |
| `from slopmortem.stages.synthesize import synthesize_all` | `from slopmortem.stages import synthesize_all` |

Preserve any `# noqa: PLC0415` comments on lazy imports verbatim. Do not collapse lazy imports that exist for `--help` performance reasons.

- [ ] **Step 3: Sibling imports inside a package stay direct; cross-package imports do NOT**

A file inside `slopmortem/corpus/` importing from another file inside `slopmortem/corpus/` is **not** a façade violation. Do not rewrite e.g. `corpus/merge.py:30 from slopmortem.corpus._db import connect` — this is sibling-internal and stays direct. Same for `corpus/sources/wayback.py` importing from `corpus/sources/_throttle.py`.

**Cross-package imports are different and MUST be rewritten.** A file inside `slopmortem/corpus/` importing from `slopmortem.llm.*` (or `slopmortem.stages.*`, etc.) is a façade violation under T1.6's `llm-leaf` contract — `slopmortem.corpus` is listed as a `source_module`. Concrete sites that must be swept:

- `slopmortem/corpus/entity_resolution.py:51` — `from slopmortem.llm.prompts import prompt_template_sha, render_prompt`
- `slopmortem/corpus/entity_resolution.py:57` — `from slopmortem.llm.client import LLMClient`
- `slopmortem/corpus/entity_resolution.py:58` — `from slopmortem.llm.embedding_client import EmbeddingClient`
- `slopmortem/corpus/summarize.py:14,17` — similar `slopmortem.llm.*` deep imports

These rewrite to `from slopmortem.llm import …`. Re-run the step 1 grep AFTER the rewrites and verify that no cross-package deep imports remain — lint-imports backstops in T1.6, but catching them here is cheaper than a contract failure.

- [ ] **Step 4: Run typecheck and tests**

Run: `just typecheck && just test`
Expected: all green. Type errors usually mean a façade is missing a re-export — go back to T1.1–T1.4 and add it, then resume.

- [ ] **Step 5: Run smoke and eval**

Run: `just smoke`
Expected: every `--help` works, eval baseline holds.

### Task T1.6: Author `.importlinter` contracts

**Files:**
- Modify: `.importlinter`
- Modify: `pyproject.toml` (pin `import-linter` in `[dependency-groups] dev` if not already pinned)

- [ ] **Step 0: Pin `import-linter` in dev deps**

If `import-linter` is not already in `pyproject.toml` `[dependency-groups] dev`, add it now (e.g. `import-linter>=2.0,<3.0`). The contracts below assume modern import-linter behavior (≥2.0 — namespace forbidden_modules and contract validation). Run `uv sync` to lock the version. Without a pin, downstream behavior (especially T4.3's `_internal` prefix-matching) is undefined.

- [ ] **Step 1: Replace the stub `.importlinter` with leaf-package contracts**

Edit `.importlinter`:

```ini
[importlinter]
root_package = slopmortem

[importlinter:contract:corpus-leaf]
name = corpus is a leaf package — outside imports go through __init__
type = forbidden
source_modules =
    slopmortem.cli
    slopmortem.ingest
    slopmortem.pipeline
    slopmortem.stages
    slopmortem.evals
    slopmortem.tracing
    slopmortem.llm
forbidden_modules =
    slopmortem.corpus.chunk
    slopmortem.corpus.disk
    slopmortem.corpus.entity_resolution
    slopmortem.corpus.extract
    slopmortem.corpus.merge
    slopmortem.corpus.merge_text
    slopmortem.corpus.paths
    slopmortem.corpus.qdrant_store
    slopmortem.corpus.reclassify
    slopmortem.corpus.reconcile
    slopmortem.corpus.tools_impl
    # add any other corpus submodules with current outside callers

[importlinter:contract:llm-leaf]
name = llm is a leaf package — outside imports go through __init__
type = forbidden
source_modules =
    slopmortem.cli
    slopmortem.ingest
    slopmortem.pipeline
    slopmortem.stages
    slopmortem.evals
    slopmortem.corpus
    slopmortem.tracing
forbidden_modules =
    slopmortem.llm.cassettes
    slopmortem.llm.client
    slopmortem.llm.embedding_client
    slopmortem.llm.embedding_factory
    slopmortem.llm.fake
    slopmortem.llm.fake_embeddings
    slopmortem.llm.fastembed_client
    slopmortem.llm.openai_embeddings
    slopmortem.llm.openrouter

[importlinter:contract:stages-leaf]
name = stages is a leaf package — outside imports go through __init__
type = forbidden
source_modules =
    slopmortem.cli
    slopmortem.ingest
    slopmortem.pipeline
    slopmortem.evals
forbidden_modules =
    slopmortem.stages.consolidate_risks
    slopmortem.stages.facet_extract
    slopmortem.stages.llm_rerank
    slopmortem.stages.retrieve
    slopmortem.stages.synthesize

[importlinter:contract:sources-leaf]
name = corpus.sources is a leaf package — outside imports go through __init__
type = forbidden
source_modules =
    slopmortem.cli
    slopmortem.ingest
    slopmortem.pipeline
    slopmortem.evals
forbidden_modules =
    slopmortem.corpus.sources.base
    slopmortem.corpus.sources.crunchbase_csv
    slopmortem.corpus.sources.curated
    slopmortem.corpus.sources.hn_algolia
    slopmortem.corpus.sources.tavily
    slopmortem.corpus.sources.wayback

[importlinter:contract:tracing-leaf]
name = tracing is a leaf package — outside imports go through __init__
type = forbidden
source_modules =
    slopmortem.cli
    slopmortem.ingest
    slopmortem.pipeline
    slopmortem.stages
    slopmortem.evals
    slopmortem.corpus
    slopmortem.llm
forbidden_modules =
    slopmortem.tracing.events

# NOTE: `ingest-private` and `cli-private` contracts are NOT defined here.
# They are added by T2.7 and T3.6 respectively, once the underlying `_*.py`
# files actually exist. import-linter rejects `forbidden` contracts with an
# empty `forbidden_modules` list, so a real placeholder is impossible — leave
# the contracts out entirely until T2.7 / T3.6 land them with concrete entries.
```

Confirm the source-module list in each contract excludes the package itself (a contract with `corpus` listed in `source_modules` against `corpus.foo` in `forbidden_modules` would fire on internal imports — that is the whole point of leaf packages).

- [ ] **Step 2: Run lint-imports**

Run: `uv run lint-imports`
Expected: 5 contracts (corpus-leaf, llm-leaf, stages-leaf, sources-leaf, tracing-leaf) all kept. There are no placeholder contracts; `ingest-private` and `cli-private` arrive in T2.7 and T3.6.

If a contract is broken, the offending file slipped past T1.5 — go back, fix the import, and re-run.

- [ ] **Step 3: Run the full gate**

Run: `just lint && just typecheck && just smoke`
Expected: green.

### Task T1.7: Rename true internals with `_` prefix

**Files:**
- Renames inside `slopmortem/corpus/`, with one commit per rename for bisectable blame.

This task is **conditional** on T1.1's verification comment. Read the header comment block in `slopmortem/corpus/__init__.py` first to confirm which modules have zero outside callers.

- [ ] **Step 1: Confirm `alias_graph` is internal-only**

Run: `grep -rnE "from slopmortem\.corpus(\.alias_graph| import alias_graph)" slopmortem/ tests/ | grep -v "^slopmortem/corpus/"`
Expected: empty output.

- [ ] **Step 2: Rename `alias_graph.py` → `_alias_graph.py`**

Run:

```bash
git mv slopmortem/corpus/alias_graph.py slopmortem/corpus/_alias_graph.py
```

Update intra-package imports inside `slopmortem/corpus/` (e.g. `from slopmortem.corpus.alias_graph import …` → `from slopmortem.corpus._alias_graph import …`). Use:

```bash
grep -lrn "slopmortem\.corpus\.alias_graph" slopmortem/
```

to find them.

- [ ] **Step 3: Run typecheck + lint + smoke**

Run: `just typecheck && just lint && just smoke`
Expected: green. **Stage and commit only the rename + intra-package import updates** to keep blame clean (`git mv ... → git commit -m "rename: alias_graph → _alias_graph"`). Do not bundle multiple renames into one commit.

- [ ] **Step 4: Confirm `schema` is internal-only**

Run: `grep -rnE "from slopmortem\.corpus(\.schema| import schema)" slopmortem/ tests/ | grep -v "^slopmortem/corpus/"`
Expected: empty output.

- [ ] **Step 5: Rename `schema.py` → `_schema.py`**

Same procedure as steps 2–3. Single commit.

- [ ] **Step 6: Decide on conditional renames**

For each of `store`, `summarize`, the verification comment in `corpus/__init__.py` tells you whether outside callers exist:
- If only `TYPE_CHECKING`-block callers and the façade re-exports the symbol, the rename is safe — proceed (one commit per rename, like steps 2–3).
- Otherwise, **skip in this PR** — list as deferred follow-up in the PR description.

For `merge_text`, `embed_sparse`, `tools_impl`: do not rename in this PR (per spec). Document the deferral in the PR description.

- [ ] **Step 7: Run the full gate one last time**

Run: `just test && just lint && just typecheck && just smoke`
Expected: green. PR 1 is ready to merge.

### PR 1 checkpoint

After T1.7, the repo state should match the spec's "End state": façades are real, deep imports fail CI, big files still big.

---

## PR 2 — Approach B for `ingest.py`

Highest-risk PR. Touches the warm-cache and journal-ordering invariants documented in `CLAUDE.md`. Cassette suite is the safety net.

**Pre-PR-2 gate (HITL):** Regenerate cassettes once on `main` before branching off. Operator-triggered, costs ~$2:

```bash
just eval-record
git add tests/fixtures/cassettes tests/evals/baseline.json
git commit -m "regenerate cassettes for PR 2 baseline"
```

If cassettes are stale at the start of PR 2, drift inside PR 2 is ambiguous.

### Task T2.1: `git mv ingest.py` → `ingest/_orchestrator.py`

**Files:**
- Move: `slopmortem/ingest.py` → `slopmortem/ingest/_orchestrator.py`
- Create: `slopmortem/ingest/__init__.py`

- [ ] **Step 1: Capture the pre-move external surface**

Run:

```bash
grep -rhnE "from slopmortem\.ingest(\.|[[:space:]])" slopmortem/ tests/ \
    | grep -v "^slopmortem/ingest" \
    | sort -u > /tmp/ingest_surface_before.txt
```

This is the list of names external callers import. T2.6 trims `__init__.py` to exactly this set.

- [ ] **Step 2: Capture pre-move smoke/eval baseline**

Run:

```bash
just smoke 2>&1 | tee /tmp/smoke_before.txt
just eval 2>&1 | tee /tmp/eval_before.txt
```

These are the diff baselines for steps 7–8.

- [ ] **Step 3: Grep for `__name__`-pinned log filters or span attributes**

Run:

```bash
grep -rnE "(slopmortem\.ingest['\"]|name == ['\"]slopmortem\.ingest['\"]|getLogger\\(['\"]slopmortem\.ingest['\"])" slopmortem/ tests/ /Users/vaporif/Repos/premortem/.github/workflows/
```

Expected: empty. If anything matches, the move will silently change `__name__` from `slopmortem.ingest` to `slopmortem.ingest._orchestrator` and break the filter — fix or update before proceeding.

- [ ] **Step 4: Move the file**

Run:

```bash
mkdir -p slopmortem/ingest
git mv slopmortem/ingest.py slopmortem/ingest/_orchestrator.py
```

- [ ] **Step 5+6: Add `__all__` to `_orchestrator.py` AND create `__init__.py` in the SAME commit**

These two edits MUST land in one commit. If `__init__.py` lands first with `from … import *` but no `__all__` on `_orchestrator.py`, the wildcard re-export leaks `InMemoryCorpus`, `FakeSlopClassifier`, `HaikuSlopClassifier`, and the test protocols to the public surface — a CLAUDE.md "fakes over mocks" violation that survives in git history.

5a. Edit the top of `slopmortem/ingest/_orchestrator.py` (after the module docstring and imports) to add an explicit `__all__` listing only the names from `/tmp/ingest_surface_before.txt`. Strip any `slopmortem.ingest.` prefix; keep just the bare symbol name. Sort alphabetically.

5b. Write `slopmortem/ingest/__init__.py`:

```python
"""Ingest pipeline: gather → slop classify → fan-out → embed → upsert."""

from __future__ import annotations

from slopmortem.ingest._orchestrator import *  # noqa: F401,F403  -- guided by _orchestrator.__all__
```

The `noqa` is required because ruff disallows `import *` by default; here it is intentional and bounded by the explicit `__all__` from 5a.

5c. Stage both files together and commit as one unit: `git add slopmortem/ingest/_orchestrator.py slopmortem/ingest/__init__.py && git commit -m "ingest: package skeleton with bounded wildcard"`.

Do not run `git commit` between 5a and 5b. T2.6 will replace this wildcard with explicit re-exports later — until then, the `__all__` is the only thing keeping fakes out of the public surface.

- [ ] **Step 7: Verify the smoke/eval baseline holds**

Run:

```bash
just smoke 2>&1 | tee /tmp/smoke_after.txt
just eval  2>&1 | tee /tmp/eval_after.txt
diff /tmp/smoke_before.txt /tmp/smoke_after.txt | head -40
diff /tmp/eval_before.txt  /tmp/eval_after.txt  | head -40
```

Expected: only timing/path differences in the smoke output; eval baseline byte-stable on the assertion summary.

- [ ] **Step 8: Run the full gate**

Run: `just test && just lint && just typecheck`
Expected: green. T1.6's `corpus-leaf` / `llm-leaf` / etc. contracts still pass (the move did not touch any cross-package imports).

### Task T2.2: Extract the warm-cache block

**Files:**
- Create: `slopmortem/ingest/_warm_cache.py`
- Modify: `slopmortem/ingest/_orchestrator.py`

- [ ] **Step 1: Identify the warm-cache block in `_orchestrator.py`**

The warm-cache pattern is "first entry runs alone, then the rest fan out" — cited in `CLAUDE.md` and `slopmortem/ingest.py` (now `_orchestrator.py`) header. Find it by searching for `CACHE_READ_RATIO_LOW` and the surrounding fan-out:

```bash
grep -nE "CACHE_READ_RATIO_LOW|first entry|prompt cache warm" slopmortem/ingest/_orchestrator.py
```

Capture the start and end line of the block plus any helper functions it depends on.

- [ ] **Step 2: Extract the block to `_warm_cache.py`**

Create `slopmortem/ingest/_warm_cache.py` with a header comment:

```python
"""Warm-cache pattern for prompt cache hit ratios.

Preserves CACHE_READ_RATIO_LOW invariant — the first entry runs alone so the
prompt prefix lands in the OpenRouter cache, then the remaining entries fan
out concurrently. See CLAUDE.md and the orchestrator caller in
`slopmortem/ingest/_orchestrator.py`.
"""

from __future__ import annotations

# (extracted code)
```

Move the block + helpers into this file. Update `_orchestrator.py` to import from `slopmortem.ingest._warm_cache`.

- [ ] **Step 3: Verify the cache_read_ratio span event still fires**

The warm-cache emits a `cache_read_ratio` span event when the ratio drops below 0.80 across the first 5 responses. Run a small sample ingest with cassettes:

```bash
just smoke
```

Inspect the cassette trace for the `cache_read_ratio` span event (or run `grep -rn cache_read_ratio tests/fixtures/cassettes/` to find the cassette that covers it).

Expected: the span event still appears in the same shape as before extraction.

- [ ] **Step 4: Run the full gate**

Run: `just test && just lint && just typecheck && just smoke && just eval`
Expected: green; eval baseline byte-stable on the assertion summary.

### Task T2.3: Extract the per-entry pipeline (`_fan_out`)

**Files:**
- Create: `slopmortem/ingest/_fan_out.py`
- Modify: `slopmortem/ingest/_orchestrator.py`

- [ ] **Step 1: Identify the per-entry pipeline seam**

Per spec, the seam is `_facet_summarize_fanout` (was `ingest.py:637`, now in `_orchestrator.py`) plus the `_embed_and_upsert` call inside `_process_entry` (was `ingest.py:688`). The actual order is **facet → summarize → embed → upsert**. Slop classification is upstream and lives in T2.5's `_slop_gate.py`, not here.

Find the seam:

```bash
grep -nE "_facet_summarize_fanout|_embed_and_upsert|_process_entry" slopmortem/ingest/_orchestrator.py
```

- [ ] **Step 2: Extract `_facet_summarize_fanout` and helpers into `_fan_out.py`**

Create `slopmortem/ingest/_fan_out.py`:

```python
"""Per-entry fan-out: facet → summarize → embed → upsert.

Slop classification gates entries upstream of fan-out (see _slop_gate.py).
"""

from __future__ import annotations

# (extracted code)
```

Move the facet/summarize/embed/upsert flow. Leave the journal-write ordering helper (the `_process_entry` body that calls `journal.upsert_pending` etc.) in `_orchestrator.py` — that's T2.4's territory.

- [ ] **Step 3: Run the full gate**

Run: `just test && just lint && just typecheck && just smoke && just eval`
Expected: green.

### Task T2.4: Extract journal write ordering

**Files:**
- Create: `slopmortem/ingest/_journal_writes.py`
- Create: `tests/ingest/test_journal_writes.py`
- Modify: `slopmortem/ingest/_orchestrator.py`

- [ ] **Step 1: Identify the journal-write block**

The actual order from the spec (line numbers from the original `ingest.py`, verify in `_orchestrator.py`):

1. `journal.upsert_pending` (`ingest.py:758`)
2. `write_raw_atomic` (`ingest.py:763`)
3. `write_canonical_atomic` (`ingest.py:780`)
4. `corpus.delete_chunks_for_canonical` (`ingest.py:797`)
5. `_embed_and_upsert` Qdrant upsert (`ingest.py:827`)
6. `journal.mark_complete` (`ingest.py:852`)

Locate these in `_orchestrator.py` (line numbers will have shifted due to T2.2 and T2.3).

- [ ] **Step 2: Write a failing test for the (Qdrant-upsert → mark_complete) gap**

Write `tests/ingest/test_journal_writes.py` (skeleton):

```python
"""Crash-recovery tests for the journal write sequence.

The invariant from CLAUDE.md: mark_complete fires only after both Qdrant and
disk writes succeed. A crash between any pair of steps must leave the journal
row in a recoverable state and never produce an orphan mark_complete.
"""

from __future__ import annotations

# imports — guided by the orchestrator's actual signatures

class _CrashAt(Exception):
    pass


def test_crash_between_upsert_pending_and_write_raw_leaves_pending_row():
    # arrange: journal + corpus that crashes inside write_raw_atomic
    # act: run _process_entry, expect _CrashAt
    # assert: journal row state == "pending"; no canonical file on disk;
    #         no Qdrant point; rerunning _process_entry recovers cleanly
    raise NotImplementedError


def test_crash_between_write_raw_and_write_canonical_leaves_recoverable_state():
    raise NotImplementedError


def test_crash_between_write_canonical_and_qdrant_upsert_leaves_recoverable_state():
    raise NotImplementedError


def test_crash_between_qdrant_upsert_and_mark_complete_no_orphan_mark_complete():
    raise NotImplementedError
```

Replace the `raise NotImplementedError` placeholders with concrete arrange/act/assert bodies that:
- inject a `Corpus` test double whose `delete_chunks_for_canonical` / `_embed_and_upsert` (or whichever boundary the test targets) raises `_CrashAt`
- run `_process_entry` and catch `_CrashAt`
- assert the journal table row's state column AND that no journal row reads `complete` for that entry
- assert disk state matches the expected canonical hash if the canonical file was written

Use `InMemoryCorpus` (already in `_orchestrator.py`) as the corpus-side base, subclassing it to inject crashes.

**Journal fake — explicit choice required.** No `InMemoryJournal` / `FakeMergeJournal` exists today; `MergeJournal` (`slopmortem/corpus/merge.py:100`) is concrete SQLite-backed. Pick ONE of these approaches and stick to it across all four tests:

- **(Preferred) Real SQLite-in-`tmp_path`.** Construct a real `MergeJournal` against `tmp_path / "journal.sqlite"`. Pros: no new fake to maintain; tests exercise the real schema and migration; matches `pytest-xdist` parallel-safety since `tmp_path` is per-test. Cons: one more I/O call per test (negligible for 4 tests).
- **(Fallback) Subclass `MergeJournal`.** Only choose this if a step needs to inject a crash *inside* a journal call (e.g. crash between `upsert_pending` writing and returning). Override the specific method, raise `_CrashAt`, then call `super().<method>()` selectively. Document why a subclass was needed in a one-line comment.

Do NOT introduce a brand-new `InMemoryJournal` class for this task — it would need to mirror the full `MergeJournal` API (15+ methods) and drift from it on schema changes. The two journal helpers in this test file (corpus subclass + crash-injection wrapper, if needed) belong in this file, not in `slopmortem/`.

- [ ] **Step 3: Run tests; expect them to fail with NotImplementedError or with assertion errors against the current code**

Run: `pytest tests/ingest/test_journal_writes.py -v`
Expected: 4 tests fail (either with `NotImplementedError` if you left placeholders, or with assertion failures because the journal write order in `_orchestrator.py` has not yet been extracted — that's fine; the test will continue passing after the move since extraction is behavior-preserving).

- [ ] **Step 4: Implement the test bodies**

Replace each `raise NotImplementedError` with the actual arrange/act/assert. Helper that subclasses `InMemoryCorpus` and `MergeJournal` to inject crashes belongs in this test file.

- [ ] **Step 5: Run tests; expect 4 passes against current `_orchestrator.py`**

Run: `pytest tests/ingest/test_journal_writes.py -v`
Expected: 4 passing tests. If any fail, the orchestrator's current write sequence violates the invariant — stop and report to operator before proceeding.

- [ ] **Step 6: Extract the journal-write block to `_journal_writes.py`**

Create `slopmortem/ingest/_journal_writes.py`:

```python
"""Per-entry journal write ordering.

Invariant (from CLAUDE.md): mark_complete fires ONLY after both Qdrant and
disk writes succeed. The order is:

    1. journal.upsert_pending
    2. write_raw_atomic
    3. write_canonical_atomic
    4. corpus.delete_chunks_for_canonical
    5. _embed_and_upsert (Qdrant)
    6. journal.mark_complete

Do NOT add a write path that bypasses this sequence. ProcessOutcome is the
return type — the orchestrator inspects it to decide whether to log a
per-entry failure or proceed.
"""

from __future__ import annotations

# (extracted code, including ProcessOutcome dataclass)
```

Update `_orchestrator.py` to call into the extracted module.

- [ ] **Step 7: Re-run the new tests AND the full gate**

Run: `pytest tests/ingest/test_journal_writes.py -v && just test && just lint && just typecheck && just smoke && just eval`
Expected: 4 new tests pass post-extraction; full gate green.

### Task T2.5: Extract slop-gate quarantine routing

**Files:**
- Create: `slopmortem/ingest/_slop_gate.py`
- Create: `tests/ingest/test_slop_gate.py`
- Modify: `slopmortem/ingest/_orchestrator.py`

- [ ] **Step 1: Identify the slop-gate routing block**

```bash
grep -nE "_quarantine|slop_threshold|_PRE_VETTED_SOURCES|slop_score" slopmortem/ingest/_orchestrator.py
```

Capture the slop-classification call site, the routing branch (above-threshold → quarantine; below-threshold → fan-out), and the `_quarantine` helper (was `ingest.py:405`).

- [ ] **Step 2: Write the failing test**

Write `tests/ingest/test_slop_gate.py`:

```python
"""Slop-gate quarantine routing.

Cassettes do not cover this path because no LLM call is made on quarantined
entries — they get no facet, no summary, no embed, no upsert. Pure unit tests
against the slop-gate module.
"""

from __future__ import annotations

# imports

async def test_above_threshold_entry_routes_to_quarantine_no_qdrant_no_journal():
    # arrange: FakeSlopClassifier returning slop_score=0.99 for one entry
    #          stub Corpus + MergeJournal that count their writes
    # act: run the slop gate against the entry
    # assert: entry written to quarantine path; corpus.upsert called 0 times;
    #         journal.upsert_pending called 0 times; journal.mark_complete called 0 times
    raise NotImplementedError


async def test_pre_vetted_source_bypasses_classifier_even_at_high_score():
    # arrange: FakeSlopClassifier that would return slop_score=0.99 if called
    #          but the entry's source_kind is in _PRE_VETTED_SOURCES
    # act: run the slop gate
    # assert: classifier was not called; entry routed to fan-out, not quarantine
    raise NotImplementedError
```

Implement both bodies. Use `FakeSlopClassifier` from `_orchestrator.py` and `InMemoryCorpus` as the test doubles.

- [ ] **Step 3: Run tests; expect them to fail until extraction**

Run: `pytest tests/ingest/test_slop_gate.py -v`
Expected: 2 fails (no `_slop_gate` module yet to import). If you wired the test to call directly into `_orchestrator.py` for the pre-extraction pass, expect 2 passes — that confirms the current behavior matches the spec.

- [ ] **Step 4: Extract slop-gate routing to `_slop_gate.py`**

Create `slopmortem/ingest/_slop_gate.py` with `_quarantine` collocated:

```python
"""Slop-gate routing.

Entries with `slop_score > config.slop_threshold` route to `_quarantine` and
get no Qdrant point and no journal row. Pre-vetted sources (in
`_PRE_VETTED_SOURCES`) bypass the classifier entirely. `--reclassify` is the
only path back from quarantine.
"""

from __future__ import annotations

# (extracted code, including _quarantine and _PRE_VETTED_SOURCES)
```

Update `_orchestrator.py` to call into the new module.

- [ ] **Step 5: Re-run the slop-gate tests AND the full gate**

Run: `pytest tests/ingest/test_slop_gate.py -v && just test && just lint && just typecheck && just smoke && just eval`
Expected: 2 slop-gate tests pass; full gate green.

### Task T2.6: Trim `ingest/__init__.py` to an explicit `__all__`

**Files:**
- Modify: `slopmortem/ingest/__init__.py`

- [ ] **Step 1: Derive the explicit public surface from current callers**

Run:

```bash
grep -rhE "from slopmortem\.ingest import [^_]" slopmortem/ tests/ \
    | grep -v "^slopmortem/ingest/" \
    | sed -E 's/.*import //' \
    | tr ',' '\n' \
    | sed -E 's/^[[:space:]]+|[[:space:]]+$//' \
    | sort -u
```

Capture the output. **Caveat (per spec):** the `[^_]` filter silently drops `_Point`, which `tests/corpus/test_qdrant_store.py:11` imports as `from slopmortem.ingest import _Point`. Verify the actual location:

```bash
grep -rnE "(class _Point|from slopmortem\.ingest import.*_Point)" slopmortem/ tests/
```

`_Point` is the Qdrant-payload dataclass currently defined at what was `slopmortem/ingest.py:219` (now `slopmortem/ingest/_orchestrator.py:219` after T2.1). It is NOT defined in `slopmortem/corpus/qdrant_store.py` — `qdrant_store.py:377` only references it in a docstring. Therefore:

- **Do NOT** rewrite the test import to `from slopmortem.corpus.qdrant_store import _Point` — that module does not export `_Point` and the import would raise `ImportError`.
- **Preferred fix:** add `_Point` to `_orchestrator.__all__` (the leading underscore is intentional — you are exporting a deliberately-private symbol for one specific test) and let it flow through `slopmortem.ingest.__init__`'s wildcard re-export. The test continues to do `from slopmortem.ingest import _Point` unchanged.
- **Alternative:** the test imports directly from the orchestrator: `from slopmortem.ingest._orchestrator import _Point`. This works but reaches past the package façade — only acceptable if T2.7's `ingest-private` contract excludes the test tree from `source_modules` (it does — only prod modules are listed).

Pick the preferred option unless `_Point` ends up moved by a later task. Do NOT redirect the import to `slopmortem.corpus.qdrant_store`.

- [ ] **Step 2: Replace the star-import init with explicit re-exports**

Edit `slopmortem/ingest/__init__.py`:

```python
"""Ingest pipeline: gather → slop classify → fan-out → embed → upsert."""

from __future__ import annotations

from slopmortem.ingest._orchestrator import (
    INGEST_PHASE_LABELS as INGEST_PHASE_LABELS,
    IngestPhase as IngestPhase,
    IngestResult as IngestResult,
    NullProgress as NullProgress,
    ingest as ingest,
    # plus every name in step 1's output, in `as` form
)

__all__ = [
    # alphabetically sorted from step 1's list
]
```

- [ ] **Step 3: Run the full gate**

Run: `just test && just lint && just typecheck && just smoke && just eval`
Expected: green. If a test breaks because of a missing name, add it to step 2's import block (and to step 1's grep output for posterity).

### Task T2.7: Add `.importlinter` contract for `slopmortem.ingest._*`

**Files:**
- Modify: `.importlinter`

- [ ] **Step 1: Add the `ingest-private` contract**

Edit `.importlinter`. T1.6 deliberately did not include a placeholder (import-linter rejects empty `forbidden_modules`). Append a fresh contract:

```ini
[importlinter:contract:ingest-private]
name = ingest._* is private — outside imports go through __init__
type = forbidden
source_modules =
    slopmortem.cli
    slopmortem.pipeline
    slopmortem.evals
    slopmortem.stages
    slopmortem.corpus
    slopmortem.llm
    slopmortem.tracing
forbidden_modules =
    slopmortem.ingest._orchestrator
    slopmortem.ingest._warm_cache
    slopmortem.ingest._fan_out
    slopmortem.ingest._journal_writes
    slopmortem.ingest._slop_gate
```

Add additional `_*.py` modules if T2.2–T2.5 introduced extras (e.g. `_protocols.py`, `_gather.py` per the destination map in the spec).

- [ ] **Step 2: Run lint-imports**

Run: `uv run lint-imports`
Expected: contract passes. If broken, an external file was importing into `ingest._*` — fix the import to go through `slopmortem.ingest` instead, or expose the symbol via T2.6's `__all__`.

- [ ] **Step 3: Run the full gate**

Run: `just test && just lint && just typecheck && just smoke && just eval`
Expected: green. PR 2 ready to merge.

### PR 2 checkpoint

`ingest.py` is now `ingest/` with named files for warm-cache, fan-out, journal-writes, slop-gate. New unit tests cover the journal-write crash boundaries and the slop-gate routing. Eval baseline byte-stable across PR 2.

---

## PR 3 — Approach B for `cli.py`

Lower risk than PR 2, but typer command registration is import-time. Sub-task ordering matters.

`slopmortem/cli_progress.py` (extracted on the cassettes branch) **stays at the top level** — the eval recorders import `RichPhaseProgress` from it. Moving it under `cli/` would force evals to reach into the future `cli/_internal/` and reintroduce the leak we are closing.

Subclass placement:
- `RichIngestProgress` (`cli.py:701`) is single-consumer (only `ingest_cmd`) → moves into `_ingest_cmd.py`.
- `RichQueryProgress` (`cli.py:739`) is dual-consumer (`query_cmd` and `replay_cmd`), shares `_QUERY_PHASE_LABELS` and `_render_query_footer` → these three names live in `cli/_common.py`.

Late/lazy imports inside `cli.py` helpers (`qdrant_client`, `MergeJournal`, `FakeSlopClassifier`, `HaikuSlopClassifier`, `ensure_collection`, `EMBED_DIMS` — at `cli.py:571, 606, 626, 629, 642, 644, 645`) exist to keep `slopmortem --help` fast. T3.2 and T3.3 must preserve the lazy `noqa: PLC0415` pattern verbatim — do not hoist these to module-top imports in the extracted files.

### Task T3.1: Move `cli.py` into a package

**Files:**
- Move: `slopmortem/cli.py` → `slopmortem/cli/_app.py`
- Create: `slopmortem/cli/__init__.py`

- [ ] **Step 1: Capture the pre-move `--help` output as a baseline**

Run:

```bash
uv run slopmortem --help > /tmp/cli_help_root_before.txt
uv run slopmortem ingest --help > /tmp/cli_help_ingest_before.txt
uv run slopmortem query --help > /tmp/cli_help_query_before.txt
uv run slopmortem replay --help > /tmp/cli_help_replay_before.txt
uv run slopmortem embed-prefetch --help > /tmp/cli_help_prefetch_before.txt
```

T3.4 will diff these against the post-extraction outputs.

- [ ] **Step 2: Move the file into the new package**

Run:

```bash
mkdir -p slopmortem/cli
git mv slopmortem/cli.py slopmortem/cli/_app.py
```

- [ ] **Step 3: Write the new `cli/__init__.py`**

Edit `slopmortem/cli/__init__.py`:

```python
"""Typer CLI entrypoint. Subcommands registered by side-effect import."""

from __future__ import annotations

from slopmortem.cli._app import app as app

__all__ = ["app"]
```

- [ ] **Step 4: Verify `pyproject.toml` `[project.scripts]` still resolves**

Read `pyproject.toml` line 29: `slopmortem = "slopmortem.cli:app"`. Expected: this still resolves because `cli/__init__.py` re-exports `app`. No edit needed.

- [ ] **Step 5: Diff the `--help` outputs**

Run:

```bash
uv run slopmortem --help > /tmp/cli_help_root_after.txt
diff /tmp/cli_help_root_before.txt /tmp/cli_help_root_after.txt
```

Expected: empty diff. Repeat for each subcommand `--help`. Any non-empty diff is a regression — stop and investigate.

- [ ] **Step 6: Run the full gate**

Run: `just test && just lint && just typecheck && just smoke`
Expected: green.

### Task T3.2: Extract one subcommand at a time

**Files:**
- Create: `slopmortem/cli/_ingest_cmd.py`
- Create: `slopmortem/cli/_query_cmd.py`
- Create: `slopmortem/cli/_replay_cmd.py`
- Create: `slopmortem/cli/_embed_prefetch_cmd.py`
- Modify: `slopmortem/cli/__init__.py`
- Modify: `slopmortem/cli/_app.py` (shrinks)

The four `@app.command(...)` registrations in `_app.py` are: `ingest`, `query`, `replay`, `embed-prefetch`. The `--reclassify` and `--reconcile` flags stay on `_ingest_cmd.py` (they are options, not subcommands). `nuke` stays in `justfile`.

**Import order in `cli/__init__.py` determines `--help` listing order.** Preserve the current ordering: `_ingest_cmd`, `_query_cmd`, `_replay_cmd`, `_embed_prefetch_cmd`.

- [ ] **Step 1: Extract `_ingest_cmd.py` (one commit)**

Move the `ingest_cmd` function and its `@app.command("ingest")` decorator into `slopmortem/cli/_ingest_cmd.py`. Also move:
- `RichIngestProgress` (single-consumer)
- The `--reclassify` and `--reconcile` flag handlers if they live in the same function body

Pattern for the new file:

```python
"""`slopmortem ingest` subcommand."""

from __future__ import annotations

# top-level imports

from slopmortem.cli._app import app


@app.command("ingest")
def ingest_cmd(...):  # body
    ...
```

Update `cli/__init__.py` to import the new module **after** `_app`:

```python
from slopmortem.cli._app import app as app
from slopmortem.cli import _ingest_cmd  # noqa: F401  -- registers @app.command("ingest")

__all__ = ["app"]
```

Run: `uv run slopmortem ingest --help && diff /tmp/cli_help_ingest_before.txt <(uv run slopmortem ingest --help)`
Expected: byte-identical help output.

Stage and commit just this extraction.

- [ ] **Step 2: Extract `_query_cmd.py` (one commit)**

Move `query_cmd` into `slopmortem/cli/_query_cmd.py`. Note: `RichQueryProgress`, `_QUERY_PHASE_LABELS`, and `_render_query_footer` are dual-consumer — DO NOT move them here. T3.3 puts them in `_common.py`. For now, leave them in `_app.py` and have `_query_cmd.py` import them from there as a temporary measure (T3.3 fixes the location).

Update `cli/__init__.py`:

```python
from slopmortem.cli import _ingest_cmd  # noqa: F401
from slopmortem.cli import _query_cmd   # noqa: F401
```

Verify `--help`. Commit.

- [ ] **Step 3: Extract `_replay_cmd.py` (one commit)**

Move `replay_cmd`. It also consumes `RichQueryProgress` — same temporary import strategy as step 2. Verify `--help`. Commit.

- [ ] **Step 4: Extract `_embed_prefetch_cmd.py` (one commit)**

Move `embed_prefetch_cmd`. Verify `--help`. Commit.

- [ ] **Step 5: Run the full gate after all four extractions**

Run: `just test && just lint && just typecheck && just smoke`
Expected: green. `_app.py` is now mostly empty except for the `app = typer.Typer(...)` definition and the dual-consumer Rich helpers.

### Task T3.3: Move shared helpers into `cli/_common.py`

**Files:**
- Create: `slopmortem/cli/_common.py`
- Modify: `slopmortem/cli/_app.py`
- Modify: `slopmortem/cli/_query_cmd.py`
- Modify: `slopmortem/cli/_replay_cmd.py`

- [ ] **Step 1: Inventory shared helpers**

The dual-consumer / shared helpers identified by the spec:
- `RichQueryProgress` (consumed by `query_cmd` and `replay_cmd`)
- `_QUERY_PHASE_LABELS` (consumed by both)
- `_render_query_footer` (consumed by both)

Plus any argument-type helpers (e.g. typer Option/Argument factories), Rich console formatters, or shared parsing helpers that show up in two or more `_*_cmd.py` files. Do a final pass:

```bash
grep -lrn "_render_query_footer\|_QUERY_PHASE_LABELS\|RichQueryProgress" slopmortem/cli/
```

- [ ] **Step 2: Move them into `_common.py`**

Create `slopmortem/cli/_common.py` and move the helpers in. Update the importers in `_query_cmd.py` and `_replay_cmd.py` to import from `slopmortem.cli._common` instead of `slopmortem.cli._app`.

Preserve the lazy `noqa: PLC0415` imports inside helper functions (the late-load pattern that keeps `--help` fast).

- [ ] **Step 3: Verify `--help` is still byte-stable**

Run: same diffs as T3.1 step 5.
Expected: empty diffs.

- [ ] **Step 4: Run the full gate**

Run: `just test && just lint && just typecheck && just smoke`
Expected: green.

### Task T3.4: Verify entrypoint and `--help` byte-stability

**Files:**
- Verify only: `pyproject.toml`, every CLI subcommand surface

- [ ] **Step 1: Re-resolve the script entrypoint**

Run: `uv run slopmortem --version 2>/dev/null || uv run slopmortem --help | head -5`
Expected: typer help banner appears, exit 0. Confirms `pyproject.toml [project.scripts] slopmortem = "slopmortem.cli:app"` still resolves through `cli/__init__.py`.

- [ ] **Step 2: Diff `--help` for every subcommand**

Run:

```bash
for sub in "" ingest query replay embed-prefetch; do
    uv run slopmortem $sub --help > /tmp/cli_help_${sub:-root}_after.txt
    diff /tmp/cli_help_${sub:-root}_before.txt /tmp/cli_help_${sub:-root}_after.txt && echo "  $sub OK"
done
```

Expected: all five diffs empty; "OK" printed five times. Any non-empty diff catches missing registrations or reordered listings.

- [ ] **Step 3: Confirm subcommand list ordering**

Run: `uv run slopmortem --help | grep -E '^  (ingest|query|replay|embed-prefetch)'`
Expected: order matches the pre-move baseline (typically `ingest`, `query`, `replay`, `embed-prefetch`).

### Task T3.5: Trim `cli/__init__.py` to `__all__ = ["app"]`

**Files:**
- Modify: `slopmortem/cli/__init__.py`
- Delete (if empty): `slopmortem/cli/_app.py`

- [ ] **Step 1: Confirm `_app.py` is empty after T3.2/T3.3**

Read `slopmortem/cli/_app.py`. After T3.2 extracted the four subcommands and T3.3 moved shared helpers to `_common.py`, `_app.py` should contain only the `app = typer.Typer(...)` construction. If anything else remains, stop and resolve before continuing — moving `app` while leaving other helpers behind will break callers of those helpers.

- [ ] **Step 2: Capture the original `typer.Typer(...)` arguments**

Before any deletion, grep the construction site so we don't lose it:

```bash
grep -A3 "typer.Typer(" slopmortem/cli/_app.py | tee /tmp/typer_construction.txt
```

Step 4 reuses this when constructing `app` in `cli/__init__.py`.

- [ ] **Step 3: Rewrite subcommand imports of `app` BEFORE deleting `_app.py`**

The four `_*_cmd.py` files currently each have `from slopmortem.cli._app import app` (planted by T3.2 step 1's pattern at the top of `_ingest_cmd.py`, etc.). After step 4 moves `app` into `cli/__init__.py`, those imports become dangling. Rewrite them to import via the package façade:

```bash
sed -i '' 's|from slopmortem\.cli\._app import app|from slopmortem.cli import app|' \
    slopmortem/cli/_ingest_cmd.py \
    slopmortem/cli/_query_cmd.py \
    slopmortem/cli/_replay_cmd.py \
    slopmortem/cli/_embed_prefetch_cmd.py
```

Verify with: `grep -rn "from slopmortem.cli" slopmortem/cli/_*_cmd.py` — every import should now be `from slopmortem.cli import app`, none should still reference `_app`.

This works because at import time, `cli/__init__.py` defines `app` BEFORE doing the side-effect import of each `_*_cmd.py` module (step 4 ordering). So when `_ingest_cmd.py` runs `from slopmortem.cli import app`, `cli` is partially initialized but `app` is already bound — Python handles the partial-init lookup correctly here.

- [ ] **Step 4: Update `cli/__init__.py`**

Edit `slopmortem/cli/__init__.py`:

```python
"""Typer CLI entrypoint. Subcommands registered by side-effect import."""

from __future__ import annotations

import typer

app = typer.Typer(no_args_is_help=True)  # match construction from /tmp/typer_construction.txt

# Side-effect imports: each module registers its @app.command() handler.
# Order determines `--help` listing order. MUST come AFTER `app` is defined
# so the subcommand modules' `from slopmortem.cli import app` resolves.
from slopmortem.cli import _ingest_cmd  # noqa: E402,F401
from slopmortem.cli import _query_cmd  # noqa: E402,F401
from slopmortem.cli import _replay_cmd  # noqa: E402,F401
from slopmortem.cli import _embed_prefetch_cmd  # noqa: E402,F401

__all__ = ["app"]
```

The `noqa: E402` is correct here (top-level imports after a non-import statement, namely `app = typer.Typer(...)`); this is distinct from `noqa: PLC0415` which the codebase uses for *lazy* imports inside function bodies. Do not switch to `PLC0415` — it would not silence E402 for these top-level imports.

Replace the `typer.Typer(no_args_is_help=True)` arguments with the actual construction captured in step 2.

- [ ] **Step 5: Delete `_app.py` if empty**

Run: `wc -l slopmortem/cli/_app.py 2>/dev/null && cat slopmortem/cli/_app.py`
If output is empty or only contains comments/whitespace: `git rm slopmortem/cli/_app.py`
If non-empty: stop — step 1's check missed something. Resolve before deleting.

- [ ] **Step 6: Run the full gate (including the `--help` byte-diff from T3.4)**

Run: `just test && just lint && just typecheck && just smoke`
Expected: green; `--help` outputs still byte-stable. If you see `ImportError: cannot import name 'app' from 'slopmortem.cli'`, step 3's sed missed a file — re-run the grep.

### Task T3.6: Add `.importlinter` contract for `slopmortem.cli._*`

**Files:**
- Modify: `.importlinter`

- [ ] **Step 1: Add the `cli-private` contract**

Edit `.importlinter`. T1.6 deliberately did not include a placeholder (import-linter rejects empty `forbidden_modules`). Append a fresh contract:

```ini
[importlinter:contract:cli-private]
name = cli._* is private — outside imports go through __init__
type = forbidden
source_modules =
    slopmortem.evals
    slopmortem.pipeline
    slopmortem.ingest
    slopmortem.stages
    slopmortem.corpus
    slopmortem.llm
    slopmortem.tracing
forbidden_modules =
    slopmortem.cli._app
    slopmortem.cli._common
    slopmortem.cli._embed_prefetch_cmd
    slopmortem.cli._ingest_cmd
    slopmortem.cli._query_cmd
    slopmortem.cli._replay_cmd
```

Drop `slopmortem.cli._app` from `forbidden_modules` if step T3.5 deleted that file.

- [ ] **Step 2: Run lint-imports**

Run: `uv run lint-imports`
Expected: contract passes. The `slopmortem/cli_progress.py` re-export pattern keeps eval recorders compliant — they import from `slopmortem.cli_progress` (top-level), not from `slopmortem.cli._*`.

- [ ] **Step 3: Run the full gate**

Run: `just test && just lint && just typecheck && just smoke`
Expected: green. PR 3 ready to merge.

### PR 3 checkpoint

`cli.py` is now `cli/` with four subcommand files and a shared `_common.py`. `--help` byte-stable across the whole refactor. `cli_progress.py` stays at the top level so evals don't reach into private internals.

---

## PR 4 — Approach C: visual `_internal/` convention (optional)

Mechanical but the largest git-history shuffle. **Re-evaluate before starting whether the underscore convention from PR 1 has proven sufficient** — if reviewers and editors find it readable enough, PR 4 is skippable.

If proceeding: single PR, reviewers diff with `git diff -M50%` to keep rename detection tight.

### Task T4.1: Move `_*.py` into `_internal/`

**Files:**
- Move (one `_internal/` per package): `slopmortem/corpus/_*.py`, `slopmortem/llm/_*.py`, `slopmortem/ingest/_*.py`, `slopmortem/cli/_*.py`

- [ ] **Step 1: Inventory the underscore files**

Run:

```bash
for pkg in corpus llm ingest cli; do
    echo "=== $pkg ==="
    ls slopmortem/$pkg/_*.py 2>/dev/null
done
```

Capture the file list. Each underscore file moves into `slopmortem/<pkg>/_internal/<name>.py` (drop the leading underscore — the directory name `_internal/` carries the visibility signal).

- [ ] **Step 2: Move `corpus` underscored files**

Run, for each `_*.py` file in `slopmortem/corpus/`:

```bash
mkdir -p slopmortem/corpus/_internal
git mv slopmortem/corpus/_alias_graph.py slopmortem/corpus/_internal/alias_graph.py
git mv slopmortem/corpus/_schema.py      slopmortem/corpus/_internal/schema.py
# repeat for every _*.py present
```

Pure file move — no edits in this step.

- [ ] **Step 3: Move `llm`, `ingest`, `cli` underscored files**

Repeat the pattern from step 2 for each of the remaining three packages.

- [ ] **Step 4: Sanity-check that nothing was missed**

Run:

```bash
ls slopmortem/corpus/_*.py slopmortem/llm/_*.py slopmortem/ingest/_*.py slopmortem/cli/_*.py 2>/dev/null
```

Expected: no output (all underscored files moved).

### Task T4.2: Update intra-package imports

**Files:**
- Inside `slopmortem/corpus/`, `slopmortem/llm/`, `slopmortem/ingest/`, `slopmortem/cli/`

- [ ] **Step 1: Find imports referencing the old underscore-prefixed paths**

Run:

```bash
grep -rnE "from slopmortem\.(corpus|llm|ingest|cli)\._[a-z]" slopmortem/
```

Each match is a path that needs rewriting from `slopmortem.<pkg>._<name>` to `slopmortem.<pkg>._internal.<name>`.

- [ ] **Step 2: Rewrite imports**

For each match, edit the importing file. Examples:
- `from slopmortem.corpus._alias_graph import …` → `from slopmortem.corpus._internal.alias_graph import …`
- `from slopmortem.ingest._warm_cache import …` → `from slopmortem.ingest._internal.warm_cache import …`
- Same pattern for every other moved file.

The `__init__.py` re-exports inside each package (from PR 1, PR 2, PR 3) also need rewriting. Find them:

```bash
grep -nE "from slopmortem\.(corpus|llm|ingest|cli)\._[a-z]" slopmortem/*/__init__.py
```

- [ ] **Step 3: Run the full gate**

Run: `just test && just lint && just typecheck && just smoke && just eval`
Expected: green. If imports are missed, basedpyright will catch them.

### Task T4.3: Tighten `.importlinter` contracts

**Files:**
- Modify: `.importlinter`

- [ ] **Step 1: Replace per-file forbidden lists with `*._internal.*` patterns**

Edit `.importlinter`. For each of the four packages, the `forbidden_modules` list collapses from "every `_*.py` file" to a single `_internal` pattern:

```ini
[importlinter:contract:corpus-internal]
name = corpus._internal.* is private — outside imports forbidden
type = forbidden
source_modules =
    slopmortem.cli
    slopmortem.ingest
    slopmortem.pipeline
    slopmortem.stages
    slopmortem.evals
    slopmortem.tracing
    slopmortem.llm
forbidden_modules =
    slopmortem.corpus._internal
```

Whether import-linter treats `slopmortem.corpus._internal` as covering everything under that namespace depends on the contract type and the installed version. T1.6 pinned `import-linter>=2.0` — for `forbidden` contracts in modern import-linter, listing a package matches the package itself but does NOT prefix-match its descendants by default unless `include_external_packages` semantics or contract option `include_descendants` is set.

**Verify before relying on prefix matching.** After step 1's edit, run `uv run lint-imports` and then deliberately add a test import like `from slopmortem.corpus._internal.foo import _bar` to a source module listed in the contract. If lint-imports does NOT fire, prefix matching is not active — fall back to listing each submodule explicitly under `forbidden_modules`:

```ini
forbidden_modules =
    slopmortem.corpus._internal
    slopmortem.corpus._internal.alias_graph
    slopmortem.corpus._internal.schema
    # ... one entry per file under _internal/
```

Remove the test import once the contract behavior is confirmed.

Repeat for `llm`, `ingest`, `cli`. The leaf-package contracts (`corpus-leaf`, `llm-leaf`, etc.) from T1.6 stay — they still forbid imports of the package's public submodules. Belt-and-braces.

- [ ] **Step 2: Run lint-imports**

Run: `uv run lint-imports`
Expected: all contracts pass.

- [ ] **Step 3: Run the full gate**

Run: `just test && just lint && just typecheck && just smoke && just eval`
Expected: green.

### Task T4.4: Update `CLAUDE.md` Layout section

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Read the current Layout section**

Read `CLAUDE.md`. Find the `## Layout` section (the tree diagram).

- [ ] **Step 2: Update the tree to reflect the new structure**

Replace the affected entries:

```
slopmortem/
  cli/                # typer entrypoints (was cli.py before PR 3)
    __init__.py       # exports `app`; registers subcommands
    _internal/        # private subcommand handlers + shared helpers
  ingest/             # source → slop → facet → summarize → qdrant fan-out (was ingest.py before PR 2)
    __init__.py       # public `ingest()` + types
    _internal/        # orchestrator, warm_cache, fan_out, journal_writes, slop_gate
  pipeline.py         # pure orchestration, deps injected
  config.py           # pydantic-settings, TOML + env precedence
  models.py           # Pydantic schemas, taxonomies
  budget.py           # cost ceilings, anyio.Lock-guarded bookkeeping
  concurrency.py      # gather_resilient + capacity limiters
  errors.py           # typed errors (BudgetExceededError, NoCannedResponseError, …)
  http.py             # safe HTTP client (SSRF guard)
  render.py           # report → markdown
  cli_progress.py     # Rich progress shared by cli/ and evals/ (top-level on purpose)
  corpus/             # public façade in __init__.py; private impl in _internal/
  stages/             # public façade in __init__.py
  llm/                # public façade in __init__.py; private impl in _internal/
  evals/              # runner, cassette recording, assertions  (NEVER imported by prod)
  tracing/            # Laminar wiring + span events
tests/                # mirrors slopmortem/ layout
docs/                 # architecture.md, cassettes.md, specs/, plans/
data/                 # crunchbase CSVs, qdrant volume (gitignored)
external/             # crunchbase-data submodule
```

- [ ] **Step 3: Add a one-paragraph explanation of `_internal/`**

Below the tree, add (or update if a similar paragraph exists):

```markdown
**`_internal/` directories** are package-private. Outside callers import from
the parent package's `__init__.py`; never reach into `*._internal.*`. CI
enforces this via `import-linter` contracts in `.importlinter`. To expose
new symbols from a `_internal/` module, add them to the package's `__all__`
in `__init__.py`.
```

- [ ] **Step 4: Run the final gate**

Run: `just test && just lint && just typecheck && just smoke && just eval`
Expected: green. PR 4 ready to merge.

### PR 4 checkpoint

Repository final state: every cross-package import goes through a public façade enforced by import-linter; private internals are visually obvious (`_internal/` directory); CLAUDE.md reflects reality.

---

## Post-PR-4: post-implementation polish

Run the `superpowers:post-implementation-polish` skill (or its successor) over the affected packages: `corpus/`, `llm/`, `stages/`, `ingest/`, `cli/`. This is parent-session work, dispatched as a single subagent at the end of execution per the Agent Assignments table. The polish pass:
- 3 review rounds with fixes
- Idiomatic-code pass
- `/cleanup`
- AI-comment strip + humanize remaining valuable comments

Polish is bounded to the diff produced by this plan; no scope creep into unrelated modules.

---

## Spec coverage self-check (author's notes)

Every section of `docs/specs/2026-05-01-encapsulation-refactor-design.md` has at least one task:
- Problem inventory (deep-import sites) → T1.1, T1.2, T1.3, T1.4 (additive façades) + T1.5 (sweep) + T1.6 (lint).
- Two oversized files (`ingest.py`, `cli.py`) → PR 2 (T2.1–T2.7) and PR 3 (T3.1–T3.6).
- Internal-only visual signal → T1.7 (underscore renames) and T4.1–T4.3 (`_internal/` directories).
- Tooling prep (import-linter, smoke recipe) → T0.1, T0.2, T0.3.
- Out-of-band fix verification → T1.0.
- Risks (PR 2 behavior drift, logger drift, cassette baseline) → T2.1 step 3 (logger grep), T2.4 (crash-boundary tests), T2.5 (slop-gate tests), pre-PR-2 cassette regen note.

No placeholders. No "TBD". No "similar to Task N" — code repeated where load-bearing. All function/symbol names checked across tasks for consistency (e.g. `set_query_corpus` introduced in T1.1 and used in T1.5 step 2's table).
