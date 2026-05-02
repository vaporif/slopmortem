# Eval Runner Cassette Fix — Design

Status: design, awaiting spec-review pass and operator approval.

Owns the completion of the work originally scoped as Tasks 6 and 7 of
`docs/plans/2026-04-29-eval-cassettes.md`. That plan's Tasks 1–5 landed
(cassette infrastructure, fixture machinery, recording helpers, justfile
wiring, and committed fixture data); Task 6 was abandoned mid-flight,
leaving the runner in a broken-by-design state. This spec is the minimum
work to finish the job.

## Problem

`slopmortem/evals/runner.py:243-244` builds the `FakeLLMClient.canned` map keyed
on a placeholder `prompt_hash = "0" * 16`. `slopmortem/llm/fake.py:104`
computes the real `prompt_hash` from `prompt + system` via
`llm_cassette_key`. The two never match, so every `just eval` run that
reaches the LLM stage raises `NoCannedResponseError`.

The recorded cassette dirs at `tests/fixtures/cassettes/evals/<row_id>/`
and the loader at `slopmortem/evals/cassettes.py:load_llm_cassettes` are
already in place — the runner just doesn't use them. `_run_deterministic`
still hand-builds canned responses with `_synthesis_payload("acme")`, so
even when the prompt-hash issue is patched the baseline ends up populated
with `"acme"` placeholder candidate IDs (visible in the current
`tests/evals/baseline.json`).

`tests/test_pipeline_e2e.py::test_full_pipeline_with_fake_clients` has a
related but distinct issue — its `_build_canned` computes real prompt hashes
but uses hand-built canned responses with placeholder candidate IDs, drifting
from production prompt rendering and recorded data. Migrating it to the
cassette pattern fixes both problems.

## Goals

- `just eval` runs against committed cassettes, exits 0, and produces a
  baseline whose candidate IDs match the recorded data.
- The `all_sources_in_allowed_domains` and `claims_grounded_in_body`
  assertions stay live in both `_run_cassettes` and `_run_live` modes (no
  silent strictness reduction).
- `test_full_pipeline_with_fake_clients` runs against a recorded cassette
  dir, preserving all (~23) contract assertions it currently checks.
- No prod code path regresses. Adding `lookup_sources` to the `Corpus`
  Protocol is the only prod-touching surface change; every Corpus
  implementation (real and fake) is updated atomically with the Protocol
  expansion so `just test` stays green throughout.

## Non-goals

- Bumping `_BASELINE_VERSION` or adding `corpus_fixture_sha256` /
  `recording_metadata` fields. The original plan included these as drift
  detection. With one operator and cassettes + fixture committed together,
  git diff catches the drift the SHA check would warn about. Skipped on
  YAGNI grounds; can be added later if silent drift becomes a real
  problem.
- Changing the public CLI surface (`--live`, `--record`, `--write-baseline`,
  `--scope`, `--max-cost-usd` keep their current shapes).
- Touching `slopmortem/ingest.py:Corpus` — separate write-side Protocol,
  same name but distinct type. Not affected by this work.
- Re-recording the existing cassettes. They were captured against the
  current pinned models and prompt templates; we trust them as-is.

## What Already Works (Tasks 1–5 Of The Old Plan)

- `slopmortem/llm/cassettes.py` — `llm_cassette_key`, `embed_cassette_key`
- `slopmortem/evals/cassettes.py` — `load_llm_cassettes`,
  `load_embedding_cassettes`, error types
- `slopmortem/evals/qdrant_setup.py` — `setup_ephemeral_qdrant` async ctx
- `slopmortem/evals/corpus_fixture.py` — `compute_fixture_sha256`, dump,
  restore
- `slopmortem/evals/recording_helper.py` — `record_cassettes_for_inputs`
- `FakeLLMClient` keyed on 3-tuple, computes real prompt_hash
- `FakeEmbeddingClient` accepts optional `canned`
- `pipeline.run_query` accepts `sparse_encoder`
- Stage code passes `prompt_template_sha` via `extra_body`
- `tests/fixtures/corpus_fixture.jsonl` (37 rows) committed
- 10 row directories under `tests/fixtures/cassettes/evals/`, plus one stale
  `.recording` artifact (cleaned up in Task 5)

## Approach

Picked option A: lift Task 6 + Task 7 of the existing plan into a fresh
dated plan, dropping the v2 baseline envelope work. Two alternatives were
considered and rejected.

### Considered alternatives

**B. Minimal runner fix — no Protocol change, no e2e test migration.**
- Pros: smallest diff (one file).
- Cons: collapses `all_sources_in_allowed_domains` to the fixed allowlist
  (regresses the strictness Task 6 was meant to add); leaves the e2e test
  broken; the broken state was the explicit motivation for this spec.

**C. Recompute real prompt_hash inside `_build_canned`.**
- Pros: tiny edit; no Protocol expansion.
- Cons: ignores the 10 committed cassette directories; hand-rolled canned
  data still produces `"acme"` placeholder candidate IDs; reinvents what
  the cassette loader already does. Not a fix, just a different bug.

**A. Wire the runner to the cassette loader, expand `Corpus` Protocol with
`lookup_sources`, migrate the e2e test, regenerate the baseline.** Picked.
- Pros: uses infrastructure that already exists; preserves assertion
  strictness in both replay and live modes; closes the broken-by-design
  gap; matches the original plan's intent.
- Cons: Protocol expansion forces every Corpus impl to add the method
  (four test fakes plus `_EvalCorpus` shim plus one prod implementation);
  requires two operator gates (regen baseline, record e2e cassettes);
  larger diff than B or C.

The cons are mechanical, not architectural. Approach A wins on every
functional axis.

## Architecture

After the change, `just eval` runs:

1. CLI parses args. Default mode (no `--live`, no `--record`) calls
   `_run_cassettes`.
2. `_run_cassettes` opens an ephemeral Qdrant collection via
   `setup_ephemeral_qdrant(corpus_fixture_path, dim)`. The returned
   `QdrantCorpus` is seeded from the committed fixture and is the same
   read-side Protocol implementation used in prod.
3. For each row in `seed.jsonl`:
   - Load cassettes from `tests/fixtures/cassettes/evals/<row_id>/`
     (`load_llm_cassettes` → 3-tuple-keyed map; `load_embedding_cassettes`
     → dense map + sparse map).
   - Build `FakeLLMClient(canned=llm_canned)` and
     `FakeEmbeddingClient(canned=dense_canned)`.
   - Build a closure `cassette_sparse(text)` that consults the sparse
     cassette dict via `embed_cassette_key(text=text, model="Qdrant/bm25")`
     and raises `NoCannedEmbeddingError` on miss.
   - Call `pipeline.run_query(ctx, llm=fake_llm, embedding_client=fake_embed,
     corpus=corpus, sparse_encoder=cassette_sparse, ...)`.
   - On `NoCannedResponseError` or `NoCannedEmbeddingError`: log
     `FAIL <row_id>: cassette miss — <key>`, set `candidates_count=0`, and
     continue to the next row. One bad row does not abort the run.
4. For each synthesized candidate, look up `sources` via
   `corpus.lookup_sources(canonical_id)` and `body` via
   `corpus.get_post_mortem(canonical_id)`. Build a `sources_map` and
   `bodies_map` keyed by candidate id, then score.
5. `--write-baseline` writes the standard `{version: 1, rows: ...}`
   envelope. No SHA, no recording metadata. Existing baseline files load
   unchanged.

`_run_live` follows the same scoring path: same `sources_map` /
`bodies_map` lookups via the prod `QdrantCorpus`. Drops the existing
"Live-mode limitation" caveat in the module docstring. Note: `_run_live`
now performs real `corpus.lookup_sources` calls; because `_FIXED_HOST_ALLOWLIST`
is narrow (`{"news.ycombinator.com"}`), live-mode assertions for
`all_sources_in_allowed_domains` may surface failures the previous live mode
silently passed (see Risks).

`_run_record` already dispatches to `record_cassettes_for_inputs` and
needs no changes.

`test_full_pipeline_with_fake_clients` migrates to the same pattern with
a single hand-recorded cassette dir at
`tests/fixtures/cassettes/e2e/test_full_pipeline_with_fake_clients/`.
The other two tests in `test_pipeline_e2e.py` keep their hand-canned
setups — they're plumbing tests, not realism tests.

## Components

### `Corpus` Protocol expansion

**File:** `slopmortem/corpus/store.py`

Adds one method:

```python
async def lookup_sources(self, canonical_id: str) -> list[str]:
    """Return the persisted source URLs for *canonical_id*, or [] if unknown.

    Used by eval scoring to compute per-candidate ``allowed_hosts`` (union
    of fixed allowlist + the candidate's own sources). Implementations
    that cannot look up payload should return [].
    """
    ...
```

**Pros:** preserves assertion strictness in both modes; small surface
addition; signature-symmetric with `get_post_mortem` (single
`canonical_id: str`, async, simple return type), but differs in error
handling — `lookup_sources` returns `[]` for unknown ids while
`get_post_mortem` raises.

**Cons:** every Corpus implementation must add the method. The
`@runtime_checkable` decorator means `isinstance(x, Corpus)` checks
attribute name presence, so a fake that omits `lookup_sources` entirely
will fail the conformance test. basedpyright covers signature correctness.
This MUST land atomically with all the test-fake updates. Caught by
`tests/corpus/test_schema_and_store.py::test_corpus_protocol_accepts_full_implementation`.

### `QdrantCorpus.lookup_sources` implementation

**File:** `slopmortem/corpus/qdrant_store.py`

Reads `payload.sources` from the underlying Qdrant point. Returns `[]`
when the canonical id is not present (caller treats absent as "use fixed
allowlist only"). Implementation per
`docs/plans/2026-04-29-eval-cassettes.md` (Task 6, Pre Step 0b).

### Per-Corpus-fake updates

Four test fakes need a trivial `async def lookup_sources(self, _: str)
-> list[str]: return []` shim. None of these tests exercise the method;
the shim is purely to satisfy the Protocol and keep the conformance test
green.

Files (each gets the shim added; no other change):
- `tests/corpus/test_schema_and_store.py` — `_Impl` (the conformance
  fixture itself; without this update, the conformance test would start
  reporting "rejects valid implementation")
- `tests/test_pipeline_e2e.py` — `_FakeCorpus`
- `tests/test_observe_redaction.py` — `_FakeCorpus`
- `tests/test_synthesis_tools.py` — `_FakeCorpus`

`tests/corpus/test_reconcile_skeleton.py::FakeCorpus` and
`tests/corpus/test_reconcile_repairs.py::_MutableCorpus` implement the
write-side `slopmortem/ingest.py:Corpus` Protocol (`has_chunks` /
`upsert_chunk` / `delete_chunks_for_canonical`), not the read-side
`slopmortem/corpus/store.py:Corpus`. They do not need `lookup_sources`.

`_EvalCorpus` in `slopmortem/evals/runner.py` must also receive the
`lookup_sources` shim in Task 1. Although it is deleted in Task 2,
expanding the Protocol in Task 1 causes basedpyright to fail at
`runner.py:484` (where `_EvalCorpus` is passed as `corpus` to
`pipeline.run_query`) before Task 2 removes it. The "After Task 1:
`just typecheck`" gate would fail without this shim. Task 1's ownership
therefore includes adding `async def lookup_sources(self, _: str) ->
list[str]: return []` to `_EvalCorpus` in `slopmortem/evals/runner.py`.

### `_run_cassettes` and rewired scoring

**File:** `slopmortem/evals/runner.py`

Replaces `_run_deterministic` with the body shown in
`docs/plans/2026-04-29-eval-cassettes.md` (Task 6B, Step 4). Key choices:

- **Pre-fetched `sources_map` and `bodies_map`** keyed by `candidate_id`,
  built from `corpus.lookup_sources` and `corpus.get_post_mortem` after
  `run_query` returns. Both modes use the same scoring path.
  - Pros: `_run_live` and `_run_cassettes` share scoring code; no
    duplicated assertion logic.
  - Cons: one extra round-trip per candidate to Qdrant (negligible at
    `K_retrieve=6` and replay scope).
- **`_score_synthesis(s, *, sources_map, bodies_map)`** — same four
  assertions as today (`where_diverged_nonempty`,
  `all_sources_in_allowed_domains`, `lifespan_months_positive`,
  `claims_grounded_in_body`). `claims_grounded_in_body` stays live in
  both modes; the existing baseline already asserts it.
- **Per-row continuation on cassette miss** — log `FAIL` and write
  `candidates_count=0` for that row, then move on. Matches the existing
  baseline's regression-as-zero-candidates semantics.

Deletes:
- `_facets`, `_payload`, `_candidate`
- `_facet_extract_payload`, `_rerank_payload`, `_synthesis_payload`
- `_build_canned`
- `_EvalCorpus`
- `_no_op_sparse_encoder`
- `_DETERMINISTIC_FACET_MODEL`, `_DETERMINISTIC_RERANK_MODEL`,
  `_DETERMINISTIC_SYNTH_MODEL`, `_DETERMINISTIC_EMBED_MODEL`
- `_build_deterministic_config`
- `_run_deterministic`

Updates the module docstring's `Modes` block to point at the cassette
directory and drops the `Live-mode limitation` paragraph.

### Runner-replay test suite

**File:** `tests/evals/test_runner_replay.py` (new)

All tests under `pytestmark = pytest.mark.requires_qdrant`. Coverage:

- End-to-end happy path: ephemeral Qdrant + cassette dir → exit 0.
- Missing cassette dir for a row: `FAIL <row_id>: no cassettes`, exit 1,
  other rows complete.
- LLM cassette miss: `NoCannedResponseError` for one row → `FAIL <row_id>:
  cassette miss`, exit 1.
- Embedding cassette miss: `NoCannedEmbeddingError` → same shape, exit 1.
- `--scope <name>` runs only that row.
- `--scope notarow` exits 2 with the valid-scopes list. (Does not reach
  Qdrant; override or omit the module-level `requires_qdrant` marker on
  this test.)
- Switching `Config.embed_model_id` between record and replay produces a
  loud `NoCannedEmbeddingError` (regression guard against silent model
  drift via the embed cassette key).
- Malformed cassette JSON for a row → `CassetteFormatError`, run-level
  exit 2 (not per-row exit 1).
- Mixed pass/fail across multiple rows: row 1 succeeds, row 2 hits
  `NoCannedResponseError` → aggregate exit code 1, row 1's results
  recorded with `candidates_count > 0`, row 2's with `candidates_count = 0`.
- Budget exceeded mid-row: `pipeline.run_query` returns
  `budget_exceeded=True` with whatever candidates accumulated. The runner
  records the row normally (NOT as a `FAIL` cassette miss) and the run
  continues; aggregate exit code reflects only cassette-miss / scoring
  failures, not budget exhaustion.

`--live` mode is not exercised in tests — operator-only, costs real
money.

### e2e test migration

**File:** `tests/test_pipeline_e2e.py` — `test_full_pipeline_with_fake_clients`
only (lines ~200-290). The other two tests in the file stay as-is.

Replaces the hand-canned `FakeLLMClient` setup with cassette loaders
pointed at `tests/fixtures/cassettes/e2e/test_full_pipeline_with_fake_clients/`.
Ports all assertions; assertions that reference the placeholder `"acme"`
candidate ID must be updated to the cassette-recorded canonical IDs.

### Cleanup

- Delete `tests/fixtures/cassettes/evals/kakikaki.36851.34fdf19c5a3e4d41bdf129cd1208dcd5.recording/`
  — leftover from a crashed recording session that was committed before
  `_sweep_stale_recording_dirs` swept it. No test references it. Keep the
  sibling `tests/fixtures/cassettes/evals/kakikaki/` directory — it's the
  live cassette dir for the `kakikaki` row.
- Final read-through of `slopmortem/evals/runner.py` module docstring,
  `--live` `--help` text, and the `_FIXED_HOST_ALLOWLIST` comment to
  confirm they read correctly post-Task 2.

## Data flow

For one `--scope <row_id>` invocation:

```
seed.jsonl (1 row)
    -> InputContext
    -> _run_cassettes
        -> setup_ephemeral_qdrant(corpus_fixture.jsonl, dim)
            -> QdrantCorpus (real, seeded)
        -> load_llm_cassettes(scope_dir) -> Mapping[(template_sha, model, prompt_hash), LlmCassette]
        -> load_embedding_cassettes(scope_dir) -> (dense_map, sparse_map)
        -> FakeLLMClient(canned=llm_canned)
        -> FakeEmbeddingClient(canned=dense_canned)
        -> cassette_sparse closure over sparse_map
        -> pipeline.run_query(ctx, llm, embedder, corpus, sparse_encoder=cassette_sparse, ...)
            -> facet_extract  -> FakeLLM lookup  -> Facets
            -> embed dense    -> FakeEmbed lookup -> dense vec
            -> embed sparse   -> cassette_sparse  -> sparse vec
            -> retrieve       -> QdrantCorpus.query -> [Candidate]
            -> llm_rerank     -> FakeLLM lookup  -> ranked
            -> synthesize     -> FakeLLM lookup  -> [Synthesis]
            -> consolidate_risks
        -> for each Synthesis:
            -> corpus.lookup_sources(candidate_id) -> list[str]
            -> corpus.get_post_mortem(candidate_id) -> str
        -> _score_report(report, sources_map, bodies_map)
    -> serialize -> diff against baseline -> exit 0/1
```

## Error handling

Per-row failures (cassette miss, embed miss) log a `FAIL` line, record
`candidates_count=0` for that row, and continue. The run as a whole exits
1 if any row failed; exits 0 otherwise. This matches the existing runner's
"per-entry failures don't abort the stage" pattern from
`docs/architecture.md`.

Run-level failures (missing `corpus_fixture.jsonl`, missing cassette
directory for `--scope`'s target row, malformed cassette JSON) print to
stderr and exit 2. Cassette parse errors come back as
`CassetteFormatError` / `CassetteSchemaError` from
`slopmortem/evals/cassettes.py` and are loud by design.

`KeyboardInterrupt` continues to surface from `anyio.run` — no special
handling needed.

## Testing strategy

Unit + integration tests under `pytest`'s default `asyncio_mode="auto"`.

- New `tests/evals/test_runner_replay.py` covers cassette replay paths
  end-to-end, marked `requires_qdrant`.
- Existing `tests/evals/test_*.py` tests for the cassette infrastructure
  (loaders, recording helper, fixture machinery, qdrant setup) keep
  running unchanged; this work doesn't touch them.
- `tests/test_pipeline_e2e.py` keeps its three tests; only
  `test_full_pipeline_with_fake_clients` migrates.
- The Protocol conformance test
  (`tests/corpus/test_schema_and_store.py::test_corpus_protocol_accepts_full_implementation`)
  is the safety net for the Protocol expansion. Updating its `_Impl`
  fixture is part of Task 1.

No fixture reuse across rows — each row owns its scope dir. Tests are
parallel-safe via `tmp_path` for any test-local fixtures.

## Operator gates

Two manual steps interleaved with the agent tasks:

1. **After Task 2 (regenerate baseline)** — the current
   `tests/evals/baseline.json` was written from the broken stub and has
   `"acme"` placeholder candidate IDs throughout. The operator runs:

   ```
   rm tests/evals/baseline.json
   docker compose up -d qdrant
   uv run python -m slopmortem.evals.runner \
     --dataset tests/evals/datasets/seed.jsonl \
     --baseline tests/evals/baseline.json \
     --write-baseline
   ```

   Then visually diffs the new file: every row from `seed.jsonl` present,
   no `"acme"` candidate IDs, every assertion `true`. Commit.

2. **Before Task 4b (record e2e cassettes)** — the operator records a
   cassette dir for `test_full_pipeline_with_fake_clients`.
   `recording_helper.py` exposes `record_cassettes_for_inputs()` as a
   library function only; the operator drives it from a one-shot script.
   (Alternatively, the operator can extend the helper with a `__main__`
   block in Task 4a if preferred.) Costs real money. Commit the cassette
   dir.

Both gates are deliberate human-in-the-loop steps because they involve
non-determinism (the live API) or destructive overwrites (the baseline
file).

## Execution Strategy

Subagents (default), sequential dispatch. Each task runs as a fresh
agent; the next task starts only after the previous task's review gate
passes and the operator gates (where applicable) complete.

Reason: tasks 1, 2, 4b, and 5 all touch overlapping module surfaces
(Protocol → runner → e2e test → docstring cleanup) and the operator gates
(Tasks 3 and 4a) sit between them. No parallel batching is possible. The
user's standing preference is sequential anyway.

## Task Dependency Graph

```
Task 1 (agent: Protocol + fakes)
  -> Task 2 (agent: runner cassette wiring)
       -> Task 3 (operator: regenerate baseline)
            -> Task 4a (operator: record e2e cassettes)
                 -> Task 4b (agent: migrate e2e test)
                      -> Task 5 (agent: cleanup)
```

Predecessor list:
- Task 1: none
- Task 2: Task 1 (needs `Corpus.lookup_sources`)
- Task 3: Task 2 (needs working runner)
- Task 4a: Task 3 (only after the runner works end-to-end is recording
  meaningful)
- Task 4b: Task 4a (needs the recorded cassette dir)
- Task 5: Task 4b (cleanup is last; nothing else should touch the runner
  after Task 5 lands)

## Agent Assignments

```
Task 1: Corpus Protocol + lookup_sources    -> python-development:python-pro  (Python)
Task 2: Runner cassette wiring              -> python-development:python-pro  (Python)
Task 3: OPERATOR (regenerate baseline)      -> human
Task 4a: OPERATOR (record e2e cassettes)    -> human
Task 4b: e2e test migration                 -> python-development:python-pro  (Python)
Task 5: Cleanup                             -> python-development:python-pro  (Python)
Polish:                                     -> python-development:python-pro  (uniform Python diff)
```

## Subagent constraints

Per user's standing preferences:

- No agent stages or commits (`git add`, `git commit`). Parent owns
  commit authorship.
- No work outside the explicit CREATE/MODIFY file list per task. If an
  agent finds a "small win" outside its ownership, it stops and reports
  rather than making the change.
- Sequential dispatch — one agent at a time, with a review gate between
  each.

## Verification gates

- After Task 1: `just typecheck && just test`. The Protocol conformance
  test in `tests/corpus/test_schema_and_store.py` is the canary for any
  missed fake.
- After Task 2: `docker compose up -d qdrant && just test -m requires_qdrant
  && just typecheck && just lint`. Runner-replay tests cover the happy
  path and the cassette-miss failure modes. Smoke check: `just eval`
  against the (still-stale) baseline — the runner reaches the cassette
  load path (no import errors / no `_EvalCorpus` references); per-row
  exit code is 1 with `FAIL` lines pointing at the stale baseline diff
  (cassettes loaded successfully but candidate IDs no longer match the
  `"acme"` baseline).
- After Task 3 (operator): visual diff of the new baseline; `just eval`
  exits 0 with no regressions.
- After Task 4a (operator): cassette dir checked in with the expected
  files (`facet_extract__*.json`, `synthesize__*.json`, `embed__*.json`
  for both dense and sparse models).
- After Task 4b: `just test tests/test_pipeline_e2e.py && just typecheck`.
  Grep the migrated test and confirm the assert count matches the original
  (~23 statements).
- After Task 5: `just test && just typecheck && just lint`. `git status`
  clean except for intentional deletions.
- Global guardrail: at the end of Tasks 2 and 4b, run `just eval`
  end-to-end as a smoke check on the recipe CI actually runs.

## Risks

- **Cassette drift if the operator changes a pinned model.** Already
  guarded — model bumps change the cassette key's `model` slot, producing
  a loud `NoCannedResponseError`. Documented in CLAUDE.md ("Don't bump
  pinned models without re-recording cassettes").
- **Recording the e2e cassette dir wrong shape.** Task 4b verifies the
  expected files exist before relying on them; a missing file produces a
  loud cassette miss in the test, not a silent skip.
- **Protocol expansion missing a fake.** The `runtime_checkable`
  Protocol + the conformance test in
  `tests/corpus/test_schema_and_store.py` catches this immediately on
  `just test`.
- **Stale cassettes after a model/prompt bump** → cassette miss /
  `NoCannedResponseError` (loud), not silent false assertion. Re-record
  the affected scope.
- **Cassette content captured a bad LLM output** → false assertions in
  the regen baseline. Re-record the affected scope; do not lower the
  assertion bar.
- **`--live` mode `all_sources_in_allowed_domains` surfacing new failures.**
  The live-mode scoring path now calls `corpus.lookup_sources`, but
  `_FIXED_HOST_ALLOWLIST` is narrow (`{"news.ycombinator.com"}`). Production
  candidates frequently have sources on non-allowlisted domains (e.g.,
  `web.archive.org` is intentionally excluded). Live mode may now surface
  assertion failures that the previous live mode silently passed. This is
  intentional strictness, not a regression.

## Out of scope (won't do here)

- Adding `corpus_fixture_sha256` or `recording_metadata` to the baseline
  envelope. YAGNI — see Non-goals.
- Refactoring `recording_helper.py` to add a `__main__` block for
  one-shot recording. Task 4a calls out the option but doesn't pre-commit
  to it; the operator decides whether to extend the helper or drive it
  from a script.
- Touching the other two tests in `test_pipeline_e2e.py`
  (`test_run_query_records_budget_exceeded`,
  `test_ctrl_c_cancels_in_flight`). They're plumbing tests, not realism
  tests.
- Live-mode tests for the runner. Operator-only, costs real money.
