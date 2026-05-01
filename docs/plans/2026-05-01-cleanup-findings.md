# Cleanup findings from the 2026-05-01 repo scan

A scan of the tree turned up three real smells worth fixing. This doc records
the fixes and their rationale so the changes don't read as random churn in
review.

The scan also flagged several things that turned out to be wrong on closer
inspection (file-wide pyright disables, the `cli.py` "Wesabe" comment, the
two `Corpus` Protocols at face value). Those are explicitly out of scope —
see "Not doing" at the end.

## Fix 1: consolidate the byte-identical `_FakeCorpus` pair

Three near-identical in-memory `Corpus` fakes exist for the read-side
protocol, but only two are in scope here:

- `tests/test_pipeline_e2e.py:250` — `_FakeCorpus`
- `tests/test_observe_redaction.py:216` — `_FakeCorpus` (docstring already
  admits "mirrors the helper in test_pipeline_e2e.py")

These two are byte-identical: same `query`, `get_post_mortem`,
`search_corpus`, and same captured-`queries` list. No `tests/conftest.py`
exists, and `tests/fixtures/` only holds cassettes/data — there's no
shared Python fixture being ignored.

`slopmortem/evals/runner.py:262` defines `_EvalCorpus` (a strict superset
adding `lookup_payload`). It stays put. It's production-side code under
`slopmortem/`; importing it from `tests/conftest.py` would be a
production-imports-from-tests anti-pattern. The `lookup_payload` extension
also gives it its own reason to exist independently. The "Mirrors the
`_FakeCorpus` from `tests/test_pipeline_e2e.py`" comment can stay too —
it's accurate context for the next reader.

### Plan

1. Create `tests/conftest.py`. Add a `FakeCorpus` dataclass with
   the three protocol methods (`query`, `get_post_mortem`,
   `search_corpus`) and the captured-`queries` list, copied verbatim from
   `tests/test_pipeline_e2e.py:250`.
2. Update `tests/test_pipeline_e2e.py` and `tests/test_observe_redaction.py`
   to import `FakeCorpus` from `tests.conftest`. Tests already instantiate
   directly (`_FakeCorpus(candidates=…)`), so a plain class export is the
   lighter change — no need for a pytest fixture wrapper.
3. Delete the two `_FakeCorpus` definitions and update call sites
   (`_FakeCorpus(...)` → `FakeCorpus(...)`).
4. Run `just test`. Both pipeline-e2e and observe-redaction suites must
   still pass with no behavior change.

`tests/test_synthesis_tools.py:26` and `tests/corpus/test_reconcile_skeleton.py:15`
are out of scope — they target different protocols (synthesis tools, write-side
respectively) with different shapes. Forcing them into the same fixture
would be a regression.

## Fix 2: move `InMemoryCorpus` out of `slopmortem/ingest.py`

`slopmortem/ingest.py` is 1106 lines. Among the orchestrator and protocol
defs sits `InMemoryCorpus` (`ingest.py:220`) — a write-side fake that no
production caller imports. Only `tests/test_ingest_dry_run.py`,
`tests/test_ingest_idempotency.py`, and `tests/test_ingest_orchestration.py`
use it.

Note: `FakeSlopClassifier` (`ingest.py:242`) stays put. `cli.py:674`
imports it for `--dry-run` mode, so it's a production fallback, not a
test-only fake. Renaming or moving it would require an `__init__.py`
re-export to preserve the public import path, which isn't worth the churn.

### Plan

1. Add `InMemoryCorpus` and its `_Point` dataclass dependency to a new
   file `tests/fakes/corpus.py` (the `tests/fakes/` directory does not
   exist yet — create it with an empty `__init__.py` so basedpyright treats
   it as a package).
2. Update the three `from slopmortem.ingest import …, InMemoryCorpus` test
   imports to pull `InMemoryCorpus` from `tests.fakes.corpus` instead.
3. Delete `_Point` and `InMemoryCorpus` from `slopmortem/ingest.py`. Verify
   no production module references either symbol (`rg "InMemoryCorpus|_Point"
   slopmortem/`). `_Point` has no other use sites; if any internal callers
   surface, leave `_Point` in `ingest.py` and import it back into
   `tests/fakes/corpus.py`.
4. Run `just test`. Tests should pass unchanged — this is a pure import path
   change.

This is the only `ingest.py` split worth doing right now. Splitting the
real protocols (`Corpus`, `IngestPhase`, `IngestProgress`, `SlopClassifier`)
into their own module is a bigger surface change with import-churn
costs across the orchestrator, classifier, and CLI. Not worth it as part
of a cleanup pass — leave it for if/when the file grows further.

## Fix 3: rename one of the two `Corpus` Protocols

Two protocols share the name `Corpus`:

- `slopmortem/ingest.py:115` — write-side (`upsert_chunk`, `has_chunks`,
  `delete_chunks_for_canonical`)
- `slopmortem/corpus/store.py:12` — read-side (`query`, `get_post_mortem`,
  `search_corpus`)

The split is intentional (interface segregation — ingest only needs the
write surface, the pipeline only needs the read surface). What's not
intentional is the shared name. `cli.py:102-103` already does
`from slopmortem.ingest import Corpus as IngestCorpus` to disambiguate, and
`slopmortem/evals/corpus_recorder.py:45` does the same. Two `as` aliases
in two different files is the smell.

### Plan

1. In `slopmortem/ingest.py`, rename the Protocol `Corpus` →
   `IngestCorpus`. Update its docstring to drop the "Narrow corpus surface"
   framing — the new name carries that meaning.
2. Update `cli.py:102` and `slopmortem/evals/corpus_recorder.py:45` to
   import the new name without an alias. Drop the `as IngestCorpus`
   suffix.
3. Search for other `from slopmortem.ingest import Corpus` occurrences
   (`rg "from slopmortem.ingest import.*Corpus"`) and update each. The
   read-side `Corpus` in `slopmortem/corpus/store.py` keeps its name —
   it's the more "public" of the two and lives in a module called
   `corpus`, so the bare name reads naturally there.
4. Run `just test` and `just typecheck` (or the project equivalent).
   basedpyright in strict mode will catch any missed import.

Auto-selected this direction (rename ingest's, keep store's) — no
downsides compared to the inverse: store's `Corpus` lives in a module
literally called `corpus`, so renaming it would force the same alias
problem onto the read side. Let me know if you disagree.

## Not doing

Findings that the validation pass debunked, kept here so they don't get
re-reported on the next scan:

- **File-wide `# pyright:` disables** (`ingest.py:1`, `openrouter.py:1`,
  `qdrant_store.py:1`, `corpus_fixture.py:1`, ~6 others). Each has a
  module docstring justifying the disable as a vendor SDK boundary.
  Inline `# pyright: ignore[...]` is also used widely (115 occurrences)
  for non-boundary cases. Both styles coexist by design.
- **The "Wesabe / collection doesn't exist" comment** in
  `slopmortem/cli.py:316-319`. It's load-bearing — explains why the
  `try/except` + stderr `Console` wrapper around the Rich live render
  exists. Without the comment, the wrapper looks redundant. Keep it.
- **`docs/ruff-check-result.md`**. Was untracked local scratch and is
  already gone from the working tree.
- **Splitting `ingest.py` more aggressively** (separate `protocols.py`,
  `classifiers.py`, `orchestrator.py`). The file is large but cohesive
  around one concern. Revisit if it grows further.
