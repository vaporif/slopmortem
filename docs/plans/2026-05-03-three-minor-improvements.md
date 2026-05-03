# Three Minor Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or `/team-feature` to implement this plan task-by-task, per the Execution Strategy below. Steps use checkbox (`- [ ]`) syntax — these are **persistent durable state**, not visual decoration. The executor edits the plan file in place: `- [ ]` → `- [x]` the instant a step verifies, before moving on. On resume (new session, crash, takeover), the executor scans existing `- [x]` marks and skips them — these steps are NOT redone. TodoWrite mirrors this state in-session; the plan file is the source of truth across sessions.

**Goal:** Cache the prompt template SHA on the hot path, fix the silent reliability-rank key mismatch for HN and Crunchbase entries, and remove file-level `# pyright: reportAny=false` headers from files that don't actually need them.

**Architecture:** Three independent edits in the ingest area. Task A is a one-line decorator on `slopmortem/llm/prompts/__init__.py`. Task B introduces a single source-name constants module under `slopmortem/corpus/sources/_names.py` and rewires the four call sites that hard-code those strings. Task C audits each file-level pyright opt-out, removes the ones the type checker doesn't actually need, and replaces genuinely needed Any leaks with narrow `cast` + comment per the project rule.

**Tech Stack:** Python 3.13, basedpyright (strict), pytest + pytest-xdist, `functools.cache`, Pydantic v2 RawEntry/Section. No new deps.

## Execution Strategy

**Subagents** — default; no spec override. Tasks A and B touch disjoint files and run in batch 1. Task C edits some of the same files as Task B (`_helpers.py`, `_slop_gate.py`) so it runs in batch 2 to avoid conflicts.

## Task Dependency Graph

- Task A [AFK]: depends on `none` → batch 1
- Task B [AFK]: depends on `none` → batch 1 (parallel with Task A)
- Task C [AFK]: depends on `Task B` → batch 2 (Task B edits `_helpers.py` and `_slop_gate.py`; Task C re-audits headers including those files)

## Agent Assignments

- Task A: prompt SHA cache → python-development:python-pro
- Task B: source-name constants + reliability-rank fix → python-development:python-pro
- Task C: pyright header audit → python-development:python-pro
- Polish: post-implementation-polish → general-purpose

---

## File Structure

**New:**
- `slopmortem/corpus/sources/_names.py` — small module exporting the canonical source-name string constants. Single source of truth for `"curated"`, `"hn_algolia"`, `"crunchbase_csv"`. Other source files import from here.

**Modified:**
- `slopmortem/llm/prompts/__init__.py` — add `@functools.cache` to `prompt_template_sha`.
- `slopmortem/ingest/_helpers.py` — rewrite `_RELIABILITY_RANK` keys to match the strings actually emitted by source modules (`hn_algolia`, `crunchbase_csv`); drop the never-matched `"wayback"` row; update the curated-provenance literal in `_build_payload` to use the constant.
- `slopmortem/ingest/_slop_gate.py` — `_PRE_VETTED_SOURCES` uses the constants instead of literal strings.
- `slopmortem/corpus/sources/curated.py`, `hn_algolia.py`, `crunchbase_csv.py` — emit the source name via the constants module.
- Various files that currently have `# pyright: reportAny=false` (Task C) — remove the header where the file type-checks clean without it; otherwise leave with a one-line comment explaining the genuine third-party Any source.

**New tests:**
- `tests/ingest/test_reliability_rank.py` — regression test asserting `_reliability_for(...)` returns the right rank for each emitted source name and the dead-letter default for unknown sources.
- `tests/test_prompts.py` — extend with a small assertion that `prompt_template_sha` is cached (call twice, check `cache_info().hits == 1`).

---

## Task A: Cache prompt template SHA on the hot path

**Why:** `prompt_template_sha` does `Path.read_bytes()` + `sha256` + slice on every call. Templates are immutable at runtime. Ten production call sites in ingest and query stages (`stages/synthesize.py`, `stages/consolidate_risks.py`, `stages/llm_rerank.py`, `stages/facet_extract.py`, `corpus/_entity_resolution.py`, `corpus/_summarize.py`, `ingest/_journal_writes.py` ×2, `ingest/_warm_cache.py`, `ingest/_impls.py`) call it per entry / per stage — many hundreds of redundant disk reads + hashes per ingest run.

**Files:**
- Modify: `slopmortem/llm/prompts/__init__.py`
- Modify: `tests/test_prompts.py`

- [x] **Step A1: Add a failing cache-hit test**

Append to `tests/test_prompts.py`:

```python
def test_prompt_template_sha_is_cached():
    from slopmortem.llm import prompt_template_sha as sha_fn

    sha_fn.cache_clear()
    _ = sha_fn("facet_extract")
    _ = sha_fn("facet_extract")
    info = sha_fn.cache_info()
    assert info.hits == 1
    assert info.misses == 1
```

- [x] **Step A2: Run the test and watch it fail**

Run: `uv run pytest tests/test_prompts.py::test_prompt_template_sha_is_cached -v`
Expected: FAIL with `AttributeError: 'function' object has no attribute 'cache_clear'` (the unwrapped function has no cache_info / cache_clear).

- [x] **Step A3: Add `@functools.cache` to `prompt_template_sha`**

Edit `slopmortem/llm/prompts/__init__.py`. At the top, add `import functools` (after the existing `import hashlib`). Then decorate the function:

```python
@functools.cache
def prompt_template_sha(name: str) -> str:
    """First 16 hex chars of sha256 over the ``.j2`` source. Used as the fixture key."""
    return hashlib.sha256(_PROMPT_DIR.joinpath(f"{name}.j2").read_bytes()).hexdigest()[:16]
```

- [x] **Step A4: Run the new test and the existing determinism test**

Run: `uv run pytest tests/test_prompts.py -v`
Expected: PASS for both `test_prompt_template_sha_is_deterministic` and the new `test_prompt_template_sha_is_cached`.

- [x] **Step A5: Run the full suite to confirm no regressions**

Run: `just test`
Expected: full suite passes.

- [x] **Step A6: Lint + typecheck**

Run: `just lint && just typecheck`
Expected: both clean.

- [x] **Step A7: Commit**

Run:
```
git add slopmortem/llm/prompts/__init__.py tests/test_prompts.py
git commit -m "perf(prompts): cache prompt_template_sha"
```

---

## Task B: Source-name constants + reliability-rank fix

**Why:** `_RELIABILITY_RANK` keys are `"hn"` and `"crunchbase"`, but the source modules emit `"hn_algolia"` and `"crunchbase_csv"`. Result: HN entries get the default rank 9 instead of 1, Crunchbase gets 9 instead of 3.

The defect is currently **dormant**: `_reliability_for` has one production caller (`_journal_writes.py:110`), v1 ingest builds a single Section per entry (see comment at `_journal_writes.py:106-107`), and `combined_text` sorts a one-element list — rank doesn't affect output. Reconcile doesn't read ranks today, and the rank is never persisted (not in the journal, not in Qdrant payloads, not in canonical front-matter). It's a latent defect that becomes live the day reconcile starts merging multi-source sections. Fix now while the change is cheap. While fixing, hoist the source-name strings to one module so the rank table, the pre-vetted set, the curated-provenance branch, and each source file all reference one symbol. Also drops the never-matched `"wayback"` row (the wayback enricher only updates `raw_html`/`markdown_text`, never sets `source="wayback"` — verified by reading `slopmortem/corpus/sources/wayback.py`).

**No `reliability_rank_version` bump.** The rank isn't persisted anywhere — it's recomputed from `entry.source` on every call to `_reliability_for`. Bumping the version would invalidate every `_skip_key` and force a full re-ingest with no observable benefit (no stored data to refresh). Leave `v1` alone.

**Pros and cons of constants location** — `slopmortem/corpus/sources/_names.py` (chosen) vs `slopmortem/corpus/sources/__init__.py`:
- Pros of dedicated `_names.py`: tiny focused module, no import cycle risk with the package's existing exports, clearly internal (underscore prefix follows the project's `_module.py` convention for internals).
- Cons: one more file in the directory.
- `__init__.py` would re-export Source/Enricher protocols and pull in lazy ONNX bits indirectly — adding constants there muddles the package surface.
- Auto-selected — focused module wins on clarity.

**Files:**
- Create: `slopmortem/corpus/sources/_names.py`
- Modify: `slopmortem/ingest/_helpers.py`
- Modify: `slopmortem/ingest/_slop_gate.py`
- Modify: `slopmortem/corpus/sources/curated.py`
- Modify: `slopmortem/corpus/sources/hn_algolia.py`
- Modify: `slopmortem/corpus/sources/crunchbase_csv.py`
- Test (new): `tests/ingest/test_reliability_rank.py`

- [x] **Step B1: Write the failing regression test**

Create `tests/ingest/test_reliability_rank.py`:

```python
"""Regression: the reliability rank table must key on the actual emitted source strings."""

from __future__ import annotations

import pytest

from slopmortem.corpus.sources._names import (
    SOURCE_CRUNCHBASE_CSV,
    SOURCE_CURATED,
    SOURCE_HN_ALGOLIA,
)
from slopmortem.ingest._helpers import _reliability_for


@pytest.mark.parametrize(
    ("source", "expected_rank"),
    [
        (SOURCE_CURATED, 0),
        (SOURCE_HN_ALGOLIA, 1),
        (SOURCE_CRUNCHBASE_CSV, 2),
    ],
)
def test_known_sources_have_explicit_rank(source: str, expected_rank: int) -> None:
    assert _reliability_for(source) == expected_rank


def test_unknown_source_lands_at_dead_letter_rank() -> None:
    assert _reliability_for("definitely-not-a-source") == 9
```

- [x] **Step B2: Run the test and watch it fail**

Run: `uv run pytest tests/ingest/test_reliability_rank.py -v`
Expected: FAIL — `slopmortem.corpus.sources._names` doesn't exist yet.

- [x] **Step B3: Create the source-name constants module**

Create `slopmortem/corpus/sources/_names.py`:

```python
"""Canonical source-identifier strings.

These are the values emitted in :class:`slopmortem.models.RawEntry.source`.
They double as keys for the reliability rank table and the pre-vetted set,
so they live in one module to keep those uses in lockstep.
"""

from __future__ import annotations

from typing import Final

SOURCE_CURATED: Final = "curated"
SOURCE_HN_ALGOLIA: Final = "hn_algolia"
SOURCE_CRUNCHBASE_CSV: Final = "crunchbase_csv"

__all__ = [
    "SOURCE_CRUNCHBASE_CSV",
    "SOURCE_CURATED",
    "SOURCE_HN_ALGOLIA",
]
```

- [x] **Step B4: Rewrite `_RELIABILITY_RANK` to use the constants and the correct keys**

Edit `slopmortem/ingest/_helpers.py`. Replace the existing `_RELIABILITY_RANK` block (currently at lines 40-46) and update `_build_payload`'s curated check:

```python
from slopmortem.corpus.sources._names import (
    SOURCE_CRUNCHBASE_CSV,
    SOURCE_CURATED,
    SOURCE_HN_ALGOLIA,
)

# merge_text orders sections by this. Curated > HN > Crunchbase > everything else.
_RELIABILITY_RANK: Final[dict[str, int]] = {
    SOURCE_CURATED: 0,
    SOURCE_HN_ALGOLIA: 1,
    SOURCE_CRUNCHBASE_CSV: 2,
}
```

In `_build_payload` (currently line 173), replace `provenance="curated_real" if provenance == "curated" else "scraped"` with:

```python
provenance="curated_real" if provenance == SOURCE_CURATED else "scraped",
```

Drop the `"wayback"` row entirely — the wayback enricher never sets `source="wayback"` (it `model_copy`s `raw_html`/`markdown_text` only).

- [x] **Step B5: Update `_PRE_VETTED_SOURCES` to use the constants**

Edit `slopmortem/ingest/_slop_gate.py:36`:

```python
from slopmortem.corpus.sources._names import SOURCE_CRUNCHBASE_CSV, SOURCE_CURATED

_PRE_VETTED_SOURCES: Final[frozenset[str]] = frozenset({SOURCE_CURATED, SOURCE_CRUNCHBASE_CSV})
```

- [x] **Step B6: Update each source module to emit the constant**

Edit `slopmortem/corpus/sources/curated.py:85` (and add the import at the top of the file):

```python
from slopmortem.corpus.sources._names import SOURCE_CURATED
```
Then replace `source="curated"` with `source=SOURCE_CURATED`.

Edit `slopmortem/corpus/sources/hn_algolia.py:78` similarly:

```python
from slopmortem.corpus.sources._names import SOURCE_HN_ALGOLIA
```
Replace `source="hn_algolia"` with `source=SOURCE_HN_ALGOLIA`.

Edit `slopmortem/corpus/sources/crunchbase_csv.py:51`:

```python
from slopmortem.corpus.sources._names import SOURCE_CRUNCHBASE_CSV
```
Replace `source="crunchbase_csv"` with `source=SOURCE_CRUNCHBASE_CSV`.

- [x] **Step B7: Run the new regression test**

Run: `uv run pytest tests/ingest/test_reliability_rank.py -v`
Expected: PASS — all three parametrize cases plus the dead-letter case.

- [x] **Step B8: Run the full suite**

Run: `just test`
Expected: PASS. The merge-order tests in `tests/corpus/test_merge_deterministic.py` construct `Section` objects with manually-passed `reliability_rank` values, so they don't exercise the rank table — they keep passing. Tests in `tests/test_cli_reconcile.py`, `tests/test_paths.py`, `tests/corpus/test_merge_journal.py`, and `tests/corpus/test_reconcile_skeleton.py` use `"hn"` as a journal key (test data, not source-emitted) and don't touch `_reliability_for` either — leave them as-is.

- [x] **Step B9: Lint + typecheck**

Run: `just lint && just typecheck`
Expected: both clean.

- [x] **Step B10: Commit**

Run:
```
git add slopmortem/corpus/sources/_names.py \
        slopmortem/ingest/_helpers.py \
        slopmortem/ingest/_slop_gate.py \
        slopmortem/corpus/sources/curated.py \
        slopmortem/corpus/sources/hn_algolia.py \
        slopmortem/corpus/sources/crunchbase_csv.py \
        tests/ingest/test_reliability_rank.py
git commit -m "fix(ingest): correct reliability rank keys for hn_algolia/crunchbase_csv"
```

---

## Task C: Audit and remove unnecessary `# pyright: reportAny=false` headers

**Why:** CLAUDE.md says "Don't add `# type: ignore` to silence basedpyright — fix the type. If a third-party stub is missing, narrow with `cast` and a one-line comment explaining why." That literally targets `# type: ignore`, but the same spirit applies to file-level `# pyright: reportAny=false` headers — they're a broader hammer. Fourteen files currently carry the file-level header. Several of them don't visibly interact with untyped libs — they're candidates for header removal in the same spirit-of-rule.

**Files (audit candidates, in priority order):**

Likely-removable (no obvious untyped-lib interaction):
- `slopmortem/ingest/_helpers.py`
- `slopmortem/ingest/_ports.py`
- `slopmortem/ingest/_slop_gate.py`
- `slopmortem/ingest/_impls.py`
- `slopmortem/ingest/_journal_writes.py`
- `slopmortem/ingest/_fan_out.py`
- `slopmortem/corpus/_merge.py`
- `slopmortem/corpus/_entity_resolution.py`

Probably needs the header (heavy untyped-lib surface — leave alone unless audit proves otherwise):
- `slopmortem/llm/openrouter.py`
- `slopmortem/llm/openai_embeddings.py`
- `slopmortem/corpus/_qdrant_store.py`
- `slopmortem/corpus/_embed_sparse.py`
- `slopmortem/evals/corpus_fixture.py` (eval-only, not prod)

- [ ] **Step C1: Establish the typecheck baseline**

Run: `just typecheck 2>&1 | tee /tmp/typecheck-before.txt; echo "exit=$?"`
Expected: clean. Save the exit code.

- [ ] **Step C2: Audit `slopmortem/ingest/_helpers.py`**

Remove the first-line `# pyright: reportAny=false` header. Run:

```
just typecheck 2>&1 | tee /tmp/typecheck-helpers.txt
```

If clean: keep the removal. If basedpyright now reports errors, inspect each. Per CLAUDE.md ("If a third-party stub is missing, narrow with cast and a one-line comment explaining why"), fix locally with `cast(...)` + a one-line `# reason` comment instead of restoring the file header. If most errors are from genuine third-party Any (e.g. `tiktoken.encode` returns `list[int]` — stubbed fine, so should not actually leak Any), restore the header and add a one-line comment justifying it.

- [ ] **Step C3: Audit `slopmortem/ingest/_ports.py`**

Same procedure: remove header, run `just typecheck`, fix or restore.

- [ ] **Step C4: Audit `slopmortem/ingest/_slop_gate.py`**

Same procedure.

- [ ] **Step C5: Audit `slopmortem/ingest/_impls.py`**

Same procedure.

- [ ] **Step C6: Audit `slopmortem/ingest/_journal_writes.py`**

Same procedure.

- [ ] **Step C7: Audit `slopmortem/ingest/_fan_out.py`**

Same procedure.

- [ ] **Step C8: Audit `slopmortem/corpus/_merge.py`**

Same procedure.

- [ ] **Step C9: Audit `slopmortem/corpus/_entity_resolution.py`**

Same procedure. This file is the largest of the eight and most likely to surface real Any leaks; budget extra attention.

- [ ] **Step C10: Final typecheck + lint + tests**

Run: `just typecheck && just lint && just test`
Expected: all green.

- [ ] **Step C11: Commit**

Run:
```
git add -u slopmortem/
git commit -m "cleanup: drop redundant pyright reportAny=false headers"
```

If any header was kept (because removing it surfaces genuine third-party Any), include a one-line comment in the file above the header explaining which library/symbol is the source of the Any leak. Mention in the commit body which files retained the header and why.

---

## Self-review notes

- Spec coverage: all three claims from the review are tasks A/B/C. ✓
- No placeholders. Every step is concrete.
- Type/name consistency: `SOURCE_CURATED`, `SOURCE_HN_ALGOLIA`, `SOURCE_CRUNCHBASE_CSV` are referenced consistently across Task B steps and the test.
- Frequent commits: one commit per task, after the task's verification passes.
- Task B fix is correctness-only (key-string mismatch); rank isn't persisted anywhere, so no `reliability_rank_version` bump and no re-ingest required.
