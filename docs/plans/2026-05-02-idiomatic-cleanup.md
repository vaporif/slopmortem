# Idiomatic cleanup — 8 verified findings from 2026-05-02 audit

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax — these are **persistent durable state**, not visual decoration. The executor edits the plan file in place: `- [ ]` → `- [x]` the instant a step verifies, before moving on. On resume, the executor scans existing `- [x]` marks and skips them — these steps are NOT redone.

**Goal:** Fix 8 idiomatic-code violations on the `cassettes` branch, surfaced by a 2026-05-02 audit and confirmed by two independent verifier agents. Three previously-flagged items were refuted (CLI-surface `print` calls in `runner.py` / `corpus_recorder.py`, file-wide pyright disables in `qdrant_store.py` / `corpus_fixture.py`, `monkeypatch.setattr` on a module-level name) and are explicitly excluded.

**Why (per-task evidence, validated):**

- **Task 1 — prod→evals import cycle.** `slopmortem/llm/fake_embeddings.py:9` imports `NoCannedEmbeddingError` from `slopmortem.evals.cassettes`. CLAUDE.md "Forbidden / discouraged" says `slopmortem.evals` must NOT be imported by `slopmortem.llm` — direction is one-way (evals → llm). The cycle is currently papered over with a lazy import at `slopmortem/evals/cassettes.py:395-401`, whose own comment acknowledges: *"fake_embeddings imports NoCannedEmbeddingError from this module, so importing anything from slopmortem.llm at module top level here re-enters cassettes.py mid-load."* `slopmortem/llm/cassettes.py` exists and explicitly declares (lines 7-9) it is the no-evals-dependency home for cassette types — the natural new home.
- **Task 2 — bare asyncio in prod.** `slopmortem/corpus/qdrant_store.py:275-279` does `import asyncio` + `await asyncio.gather(...)` to fan out `_fetch_aliases` per candidate. CLAUDE.md says async via `anyio` only. The gather is all-or-nothing (one failure must abort the whole alias-collapse step — partial edge data corrupts dedup), so `gather_resilient` is the wrong replacement; use `anyio.create_task_group` (anyio 4.x does NOT expose a top-level `anyio.gather()` — the task group is the only available primitive).
- **Task 3 — untyped tests.** `tests/llm/test_embedder_factory.py:12,19,27` defines test functions without annotations (`def test_...():`, `def test_...(monkeypatch):`). The prevailing style across the changed test set annotates `monkeypatch: pytest.MonkeyPatch` and `-> None` (e.g. `tests/sources/test_curated.py:58`).
- **Task 4 — `print` band-aid in CLI.** `slopmortem/cli.py:554` does `print(..., file=sys.stderr)` with `# noqa: T201` for the LMNR-key-missing warning. The rest of `cli.py` uses `typer.echo(..., err=True)` (line 846) and `err_console.print` inside the query/ingest commands (e.g. line 479). The `noqa` suppression is itself a signal the author knew this was inconsistent.
- **Task 5 — redundant pytest decorators.** `tests/sources/test_curated.py:57,94,132` has `@pytest.mark.asyncio` decorators. `pyproject.toml:52` sets `asyncio_mode = "auto"`, making them dead.
- **Task 6 — pathlib regression.** `slopmortem/evals/corpus_recorder.py:243` uses `os.replace(out_tmp, out_path)` with `# noqa: PTH105 — atomic POSIX rename`. `Path.replace()` wraps `os.replace` and is equally atomic; switching drops both the `os` import dependency at this site and the noqa.
- **Task 7 — pyright noise localization.** `slopmortem/evals/recording.py:105-107` has three consecutive `# pyright: ignore[reportAny]` for a single `getattr(t, "name", ...)` loop over an untyped `tools` parameter. A small `_tool_name(t: object) -> str` helper collapses the three ignores to one site.
- **Task 8 — stale CLI breadcrumb + test docstring drift.** `slopmortem/cli.py` has THREE "Task 11" references: the user-facing error at line 792 (`"no dataset at {path}; ship Task 11"`) leaks an internal task-tracking artifact into end-user output, and the module docstring (line 11) plus a function docstring (line 783) carry the same stale phrasing. `tests/test_eval_runner.py:218` docstring says "via asyncio.run" but `runner.py:562` uses `anyio.run`.

**Tech Stack:** Python 3.13, anyio, pydantic v2, pytest (`asyncio_mode=auto`, xdist), basedpyright (strict), typer/Rich.

## Priority

| Task | Pri | Type | Impact if skipped |
|---|---|---|---|
| **Task 1** Move `NoCannedEmbeddingError` to `slopmortem.llm.cassettes` | P1 | Convention (forbidden import direction) | Runtime cycle survives. Lazy-import workaround stays. CLAUDE.md "Forbidden" violation. |
| **Task 2** Replace `asyncio.gather` with anyio | P1 | Convention (anyio-only rule) | Bare asyncio in prod. Sets a precedent. |
| **Task 3** Annotate `test_embedder_factory.py` | P2 | Style consistency | Inconsistent with rest of test suite. |
| **Task 4** Replace `print` with `typer.echo` in `cli.py:554` | P2 | Convention | Inconsistent stderr surface within `cli.py`. |
| **Task 5** Drop redundant `@pytest.mark.asyncio` (23 across 8 files) | P3 | Cleanup | Dead decorators; misleads readers about config. |
| **Task 6** `os.replace` → `Path.replace` | P3 | pathlib idiom | Drops `os` dependency at site; minor. |
| **Task 7** `_tool_name` helper in recording.py | P3 | Pyright-noise reduction | Cosmetic; helper localizes ignore. |
| **Task 8** Stale "Task 11" breadcrumbs (×3 in cli.py) + docstring drift | P3 | Hygiene | Minor confusion; user-visible at line 792. |

## Out of scope (explicitly excluded)

- **`tests/sources/test_curated.py` `unittest.mock.AsyncMock` migration** — there's no existing fake for `slopmortem/http.py:safe_get` / `respect_robots` / `throttle_for`. Building a fake is a bigger refactor than the convention violation justifies right now. Leave the mocks in place.
- **`tests/test_cli_embed_prefetch.py:25` `_load_sync` private-attr DI seam** — requires adding a constructor parameter to `FastEmbedEmbeddingClient`. Defer.
- **`runner.py` 12 `print` consolidation** — `runner.py` is a CLI surface (`__main__` guarded). CLAUDE.md's "no print in library code" rule does not apply.
- **`corpus_recorder.py:286` `print` for RUN_LIVE-missing** — same: CLI surface, not library.
- **`qdrant_store.py:1` / `corpus_fixture.py:1` file-wide pyright disables** — disable distinct pyright flags from the per-site `reportExplicitAny` ignores; dropping the file-wide directives would surface a wave of new errors from the Qdrant SDK boundary.
- **`tests/test_cli_progress.py:99,127` `monkeypatch.setattr(corpus_recorder, "_RichIngestProgress", _Spy)`** — patches a module-level name, which is standard pytest practice. Not a private-attr access.
- **`cli.py:322-323` "asyncio.CancelledError" comment** — imprecise wording but behaviorally correct on the asyncio anyio backend; leave alone.

## Execution Strategy

**Sequential, single session, one task at a time.** Per project preference: do not parallelize tasks, do not dispatch parallel subagents for execution. Each task lists explicit **CREATE / MODIFY** files. Stay within that list — no tangential dep bumps, refactors, or "small wins" outside the listed scope.

After each task: run targeted tests (`just test -k <pattern>`), then `just lint` and `just typecheck` for the touched files, confirm green, mark steps `[x]`, commit with a terse subject (`fix`, `cleanup`, `idiomatic pass`) before starting the next task. Do not batch commits across tasks.

---

## Task 1 — Move `NoCannedEmbeddingError` out of `slopmortem.evals`

**Files:**
- Modify: `slopmortem/llm/cassettes.py` (add the error type)
- Modify: `slopmortem/evals/cassettes.py` (re-export from new location; drop lazy-import workaround)
- Modify: `slopmortem/llm/fake_embeddings.py` (import from `slopmortem.llm.cassettes`)
- Modify: `slopmortem/evals/runner.py` (import from `slopmortem.llm.cassettes`)
- Modify: `slopmortem/concurrency.py` (update stale `slopmortem.evals.cassettes` reference in `gather_resilient` docstring at line 25)
- Modify: `tests/test_cassettes.py` (import update — verify the existing import path keeps working via re-export, but prefer the canonical new path)

**Decision:** Move the class to `slopmortem.llm.cassettes` (already structured to be the cycle-free home for cassette types) and keep a re-export in `slopmortem.evals.cassettes` so external importers don't break. Rejected: leaving it in `evals` and continuing the lazy-import workaround — that papers over a documented "Forbidden" rule and the lazy import is itself a code smell that future maintenance will trip on.

**Pros / Cons:**
- Pros: eliminates the runtime cycle entirely; lets `slopmortem.evals.cassettes` drop its lazy import block (lines 390-396) and import `FakeEmbeddingClient` at module top; aligns with the docstring contract `slopmortem/llm/cassettes.py:7-9` already advertises.
- Cons: one new symbol exported from `slopmortem.llm.cassettes`. Negligible.

**Steps:**

- [x] **Step 1: Add `NoCannedEmbeddingError` to `slopmortem/llm/cassettes.py`.**

  Append the class definition (copy verbatim from `slopmortem/evals/cassettes.py:50-56`, including docstring) at the bottom of `slopmortem/llm/cassettes.py`. Subclass `BaseException` (not `Exception`) — preserve the existing semantic that resilient fan-out wrappers can't swallow it.

- [x] **Step 2: Re-export from `slopmortem/evals/cassettes.py`.**

  Replace the existing `class NoCannedEmbeddingError(BaseException):` block at `slopmortem/evals/cassettes.py:50-56` with a re-export:

  ```python
  from slopmortem.llm.cassettes import NoCannedEmbeddingError as NoCannedEmbeddingError
  ```

  (The `as NoCannedEmbeddingError` form satisfies `__all__`-implicit re-export linting.)

- [x] **Step 3: Update `slopmortem/llm/fake_embeddings.py:9`.**

  Change `from slopmortem.evals.cassettes import NoCannedEmbeddingError` to `from slopmortem.llm.cassettes import NoCannedEmbeddingError`.

- [x] **Step 4: Update `slopmortem/evals/runner.py:85`.**

  Change `NoCannedEmbeddingError` import from `slopmortem.evals.cassettes` to `slopmortem.llm.cassettes`. (`NoCannedResponseError` may still come from `evals.cassettes`; only move `NoCannedEmbeddingError`.)

- [x] **Step 5: Drop the lazy-import workaround in `evals/cassettes.py`.**

  At `slopmortem/evals/cassettes.py:395-401`, the function `load_row_fakes` lazily imports `embed_cassette_key` (399), `FakeLLMClient`/`FakeResponse` (400), and `FakeEmbeddingClient` (401) *because* importing `slopmortem.llm.fake_embeddings` at module top would trigger the cycle. With the cycle gone (Task 1 done), promote these to **unconditional module-level imports** at the top of the file — NOT into the `TYPE_CHECKING` block, because all three are instantiated at runtime in `load_row_fakes` (e.g. `FakeLLMClient(...)` around line 414, `FakeEmbeddingClient(...)` shortly after). The pre-existing `TYPE_CHECKING` import of `FakeEmbeddingClient` at line 28 becomes redundant once the runtime import exists; remove it. Then delete the explanatory comment block (lines 395-398) plus the three `# noqa: PLC0415` lazy-import lines (399-401) verbatim.

- [x] **Step 6: Update stale docstring in `slopmortem/concurrency.py:25`.**

  The `gather_resilient` docstring references "the cassette-miss errors in `slopmortem.llm.fake` / `slopmortem.evals.cassettes`". After Task 1 the canonical home of `NoCannedEmbeddingError` is `slopmortem.llm.cassettes`. Update the reference to `slopmortem.llm.fake` / `slopmortem.llm.cassettes`. Docstring only, no code change.

- [x] **Step 7: Update `tests/test_cassettes.py:18`.**

  The test imports `NoCannedEmbeddingError` from `slopmortem.evals.cassettes`. Re-export from Step 2 keeps it working, but prefer the canonical path: change to `from slopmortem.llm.cassettes import NoCannedEmbeddingError`. Keep other imports (e.g. `NoCannedResponseError`) from `slopmortem.evals.cassettes` as-is.

- [x] **Step 8: Verify.**

  Run `just typecheck` (must pass — basedpyright will catch any missed import). Run `just test -k "cassette or fake_embed or embedder"`. Run `just lint`. Confirm no `from slopmortem.evals` import survives in any file under `slopmortem/llm/`:

  ```
  grep -rn "from slopmortem.evals" slopmortem/llm/
  ```

  Expected output: empty.

- [x] **Step 9: Commit.** Subject: `cleanup: move NoCannedEmbeddingError to slopmortem.llm.cassettes`.

---

## Task 2 — Replace `asyncio.gather` with anyio in qdrant_store

**Files:**
- Modify: `slopmortem/corpus/qdrant_store.py`

**Decision:** Use `anyio.create_task_group`. anyio 4.x does NOT expose a top-level `anyio.gather()` function (verified: zero matches in the codebase, not in the public anyio surface). The task-group form is the only available primitive. Rejected: `slopmortem.concurrency.gather_resilient` — that's continue-on-error semantics. The alias-edge fan-out is all-or-nothing: one failed `_fetch_aliases` corrupts the alias graph and `collapse_alias_components` would silently under-collapse. We want one failure to abort the whole alias-dedup step (the caller already returns un-collapsed candidates if `_fetch_aliases is None`, which is the correct fallback path).

**Pros / Cons:**
- Pros: removes the only `import asyncio` in the prod tree (excluding the legitimate use in `slopmortem/concurrency.py`); aligns with the project rule.
- Cons: slightly more verbose than `asyncio.gather`. Trivial.

**Steps:**

- [x] **Step 1: Replace lines 274-279 in `slopmortem/corpus/qdrant_store.py`.**

  Current code at `qdrant_store.py:274-281`:

  ```python
  if self._fetch_aliases is not None and candidates:
      import asyncio  # noqa: PLC0415 — local to keep top-level imports lean

      edge_lists = await asyncio.gather(
          *(self._fetch_aliases(c.canonical_id) for c in candidates)
      )
      edges: list[AliasEdge] = [e for sub in edge_lists for e in sub]
      candidates = collapse_alias_components(candidates, edges)
  ```

  Replace with the anyio task-group form:

  ```python
  if self._fetch_aliases is not None and candidates:
      edge_lists: list[list[AliasEdge]] = [[] for _ in candidates]

      async def _collect(idx: int, cid: str) -> None:
          assert self._fetch_aliases is not None  # type-narrow inside closure
          edge_lists[idx] = await self._fetch_aliases(cid)

      async with anyio.create_task_group() as tg:
          for i, c in enumerate(candidates):
              tg.start_soon(_collect, i, c.canonical_id)

      edges: list[AliasEdge] = [e for sub in edge_lists for e in sub]
      candidates = collapse_alias_components(candidates, edges)
  ```

  This preserves all-or-nothing semantics: any task raising propagates out of the task group's `__aexit__` (anyio cancels siblings), aborting the alias-collapse step exactly as `asyncio.gather` did.

- [x] **Step 2: Add `import anyio` to module-top imports of `slopmortem/corpus/qdrant_store.py`.**

  Verified: `qdrant_store.py` does NOT currently import `anyio` at module top (zero matches). Add `import anyio` to the top-of-file import block. (The local `import asyncio` is gone, so no replacement is needed at the call site.)

- [x] **Step 3: Verify.**

  ```
  grep -n "import asyncio\|asyncio\." slopmortem/corpus/qdrant_store.py
  ```

  Expected output: empty.

  Run `just test -k "qdrant or alias or retrieve"` and confirm green. Run `just typecheck`.

- [x] **Step 4: Commit.** Subject: `fix: replace asyncio.gather with anyio task group in qdrant_store`.

---

## Task 3 — Annotate `test_embedder_factory.py`

**Files:**
- Modify: `tests/llm/test_embedder_factory.py`

**Decision:** Match the prevailing style: `monkeypatch: pytest.MonkeyPatch` arg type and `-> None` return.

**Steps:**

- [x] **Step 1: Annotate three test functions.**

  At `tests/llm/test_embedder_factory.py:12,19,27`, change:

  ```python
  def test_factory_returns_fastembed_for_fastembed_provider():
  def test_factory_returns_openai_for_openai_provider(monkeypatch):
  def test_factory_raises_on_unknown_provider():
  ```

  To:

  ```python
  def test_factory_returns_fastembed_for_fastembed_provider() -> None:
  def test_factory_returns_openai_for_openai_provider(monkeypatch: pytest.MonkeyPatch) -> None:
  def test_factory_raises_on_unknown_provider() -> None:
  ```

  Verify `import pytest` is already present at the top of the file (it is needed for `pytest.MonkeyPatch`); add if missing.

- [x] **Step 2: Verify.**

  Run `just test -k test_embedder_factory` and `just typecheck`. Both green.

- [x] **Step 3: Commit.** Subject: `cleanup: annotate test_embedder_factory.py`.

---

## Task 4 — Replace `print` band-aid in `cli.py:554`

**Files:**
- Modify: `slopmortem/cli.py`

**Decision:** Use `typer.echo(..., err=True)` to match `cli.py:846`. (`err_console.print` is also valid but `typer.echo` is the closer behavioral match for a one-line stderr write that doesn't need Rich markup.)

**Steps:**

- [x] **Step 1: Replace lines 554-557.**

  Current code at `cli.py:553-558`:

  ```python
  if not api_key:
      print(  # noqa: T201 - CLI surface; intentional stderr write
          "slopmortem: LMNR_PROJECT_API_KEY missing; tracing disabled",
          file=sys.stderr,
      )
      return
  ```

  Replace with:

  ```python
  if not api_key:
      typer.echo(
          "slopmortem: LMNR_PROJECT_API_KEY missing; tracing disabled",
          err=True,
      )
      return
  ```

- [x] **Step 2: Drop unused `sys` import if applicable.**

  Check whether `sys` is still used elsewhere in `cli.py` after the edit:

  ```
  grep -n "^import sys\|sys\." slopmortem/cli.py
  ```

  If only this site referenced `sys`, remove the import. Otherwise leave it.

- [x] **Step 3: Verify.**

  Run `just lint` (the `T201` noqa is gone; ruff must not complain). Run `just test -k "tracing or _maybe_init"` if any such test exists; otherwise smoke-run `just query "anything"` to confirm the CLI still loads.

- [x] **Step 4: Commit.** Subject: `cleanup: typer.echo for missing-LMNR-key warning`.

---

## Task 5 — Drop redundant `@pytest.mark.asyncio` decorators (entire test suite)

**Files:**
- Modify: `tests/test_reclassify.py` (3 occurrences: lines 18, 68, 110)
- Modify: `tests/test_list_review.py` (1 occurrence: line 33)
- Modify: `tests/test_synthesis_tools.py` (4 occurrences: lines 108, 131, 152, 175)
- Modify: `tests/sources/test_hn_algolia.py` (2 occurrences: lines 56, 100)
- Modify: `tests/sources/test_curated.py` (3 occurrences: lines 57, 94, 132)
- Modify: `tests/sources/test_wayback.py` (3 occurrences: lines 36, 60, 114)
- Modify: `tests/sources/test_crunchbase_csv.py` (2 occurrences: lines 23, 40)
- Modify: `tests/sources/test_tavily_enricher.py` (5 occurrences: lines 26, 39, 52, 90, 105)

**Decision:** `pyproject.toml:52` sets `asyncio_mode = "auto"`. All 23 `@pytest.mark.asyncio` decorators across 8 test files are dead. Originally scoped to `test_curated.py` only, but verification (`grep -rn "@pytest.mark.asyncio" tests/ | wc -l` → 23) shows the same convention violation everywhere. One pass fixes it all; partial scope leaves identical lint debt elsewhere.

**Steps:**

- [x] **Step 1: Remove all `@pytest.mark.asyncio` decorator lines across the 8 files listed above.** Verify with:

  ```
  grep -rn "@pytest.mark.asyncio" tests/
  ```

  Expected output: empty.

- [x] **Step 2: Verify `pytest` imports remain live in each file.**

  In each touched file, confirm `pytest` is still referenced (e.g. `pytest.MonkeyPatch` annotations, `pytest.raises`, `pytest.fixture`). If `@pytest.mark.asyncio` was the sole reference, prune the unused import. Spot-check by running `just lint` after the edits — ruff `F401` will flag any newly-unused `pytest` import.

- [x] **Step 3: Verify.**

  Run the full test suite: `just test`. All previously-async tests must still execute as async (collection errors would surface immediately). Run `just lint`.

- [x] **Step 4: Commit.** Subject: `cleanup: drop redundant @pytest.mark.asyncio decorators`.

---

## Task 6 — `os.replace` → `Path.replace` in corpus_recorder

**Files:**
- Modify: `slopmortem/evals/corpus_recorder.py`

**Decision:** `Path.replace()` is also atomic (it wraps `os.replace`). Drops the `# noqa: PTH105` and one `os` reference.

**Steps:**

- [x] **Step 1: Replace `corpus_recorder.py:243`.**

  Current:

  ```python
  os.replace(out_tmp, out_path)  # noqa: PTH105 — atomic POSIX rename
  ```

  Replace with:

  ```python
  out_tmp.replace(out_path)
  ```

  Confirm `out_tmp` is a `Path` by reading lines 240-243 (it is — `out_path.with_suffix(...)`).

- [x] **Step 2: Check whether `os` import is still needed.**

  ```
  grep -n "^import os\|os\." slopmortem/evals/corpus_recorder.py
  ```

  Other call sites likely keep it live (e.g. `os.environ.get("RUN_LIVE")` at line 285). Leave the import as-is unless this was the last use.

- [x] **Step 3: Verify.**

  Run `just lint` (PTH105 noqa gone). Run `just test -k "corpus_recorder or recorder"`.

- [x] **Step 4: Commit.** Subject: `cleanup: Path.replace in corpus_recorder`.

---

## Task 7 — Localize pyright ignores in `recording.py`

**Files:**
- Modify: `slopmortem/evals/recording.py`

**Decision:** Extract a `_tool_name(t: object) -> str` helper. Three `# pyright: ignore[reportAny]` collapse to one. The underlying type fuzziness (untyped `tools` parameter) is unchanged; this is a noise-reduction nit, not a type-correctness fix.

**Steps:**

- [x] **Step 1: Add a module-private helper above the recording method.**

  Before the function containing lines 104-107, add:

  ```python
  def _tool_name(t: object) -> str:
      """Best-effort string name for a tool object; falls back to `str(t)`."""
      name = getattr(t, "name", None)
      return str(name) if name is not None else str(t)  # pyright: ignore[reportAny]
  ```

- [x] **Step 2: Replace lines 104-107 in the consuming function.**

  Current:

  ```python
  tool_names: list[str] = []
  for t in tools or []:  # pyright: ignore[reportAny]
      name_attr: object = getattr(t, "name", None)  # pyright: ignore[reportAny]
      tool_names.append(str(name_attr) if name_attr is not None else str(t))  # pyright: ignore[reportAny]
  ```

  Replace with:

  ```python
  tool_names: list[str] = [_tool_name(t) for t in tools or []]  # pyright: ignore[reportAny]
  ```

  (One ignore on the comprehension covers the untyped `tools` iteration.)

- [x] **Step 3: Verify.**

  Run `just typecheck` (basedpyright must remain green; the helper's narrowing closes off two of the three sites). Run `just test -k "recording or cassette"`.

- [x] **Step 4: Commit.** Subject: `cleanup: _tool_name helper in recording.py`.

---

## Task 8 — Stale breadcrumb + docstring drift

**Files:**
- Modify: `slopmortem/cli.py`
- Modify: `tests/test_eval_runner.py`

**Decision:** Bundle the four one-line fixes (three in `cli.py`, one in `test_eval_runner.py`) into one commit since they're the same hygiene class (stale text).

**Steps:**

- [x] **Step 1: Replace `cli.py:792` (user-facing string).**

  Current:

  ```python
  typer.echo(f"no dataset at {path}; ship Task 11", err=True)
  ```

  Replace with a useful instruction:

  ```python
  typer.echo(f"no dataset at {path}; run 'just eval-record' to generate it", err=True)
  ```

  (Verify the recipe name. `grep "^eval-record" justfile` should match.)

- [x] **Step 2: Update `cli.py:11` (module docstring).**

  Current line 11: `` ``replay`` iterates an evals dataset (format + content shipped in Task 11). The ``

  Replace `(format + content shipped in Task 11)` with `(JSONL, one InputContext per line)` so the docstring describes the shape rather than referencing a defunct task ID.

- [x] **Step 3: Update `cli.py:783` (function docstring of `replay`).**

  Current line 783: `    row goes to stdout. Dataset format ships with Task 11.`

  Replace `Dataset format ships with Task 11.` with `Dataset format: JSONL, one InputContext per line.`

- [x] **Step 4: Replace `tests/test_eval_runner.py:218`.**

  Current docstring:

  ```python
  """--record dispatches to record_cassettes_for_inputs via asyncio.run."""
  ```

  Replace with:

  ```python
  """--record dispatches to record_cassettes_for_inputs via anyio.run."""
  ```

- [x] **Step 5: Verify.**

  ```
  grep -n "Task 11\|asyncio.run" slopmortem/cli.py tests/test_eval_runner.py
  ```

  Expected: no matches in these files (other matches elsewhere are out of scope).

  Run `just test -k "test_runner_record_flag or test_replay"`.

- [x] **Step 6: Commit.** Subject: `cleanup: stale Task 11 breadcrumbs and asyncio.run docstring`.

---

## Sign-off

After all 8 tasks land:

- [ ] `just lint` clean
- [ ] `just typecheck` clean
- [ ] `just test` clean
- [ ] `just eval` (cassettes) clean — confirms no behavior regression (NoCannedEmbeddingError relocation must not break replay)
- [ ] No new `from slopmortem.evals` import in any file under `slopmortem/llm/`
- [ ] No `import asyncio` in any file under `slopmortem/` **except** `slopmortem/concurrency.py` (legitimate use: foundation of `gather_resilient`, which intentionally wraps `asyncio.gather(return_exceptions=True)`) and any test-only stub remaining under `slopmortem/evals/`
- [ ] No `@pytest.mark.asyncio` decorator in any file under `tests/` (`asyncio_mode = "auto"` makes them dead)
