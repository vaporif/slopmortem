# Cassette Recording — Rich UI

**Date:** 2026-05-02
**Status:** Approved

## Problem

Both live cassette-recording entry points run silently while burning real OpenRouter spend:

- `just eval-record` (`slopmortem.evals.runner --record`) loops over the dataset calling `record_cassettes_for_inputs()`, which in turn drives `run_query()` through facet/rerank/synthesize per row. Nothing renders during the run; the operator sees output only after the post-run PASS/FAIL pass.
- `just eval-record-corpus` (`slopmortem.evals.corpus_recorder`) calls `ingest()` against a throwaway Qdrant collection. It prints exactly one line at the end: `wrote tests/fixtures/corpus_fixture.jsonl (NNNNN bytes)`.

The interactive `slopmortem ingest` and `slopmortem query` paths already use Rich Live progress with per-phase bars, error counters, and post-run summary panels. Recording deserves the same UX since it's the most expensive thing the project does (live API calls under a `--max-cost-usd` cap).

## Architecture

The pipeline already supports progress reporting through `IngestProgress` / `QueryProgress` Protocols. No pipeline change is needed — only the recording wrappers, the CLI seams, and the underlying progress machinery move.

### Two changes, in order

**Step 1 — Promote `_RichPhaseProgress` to a shared module.**

Move from `slopmortem/cli.py` to a new `slopmortem/cli_progress.py`:
- `_RichPhaseProgress[PhaseT]` → renamed to `RichPhaseProgress[PhaseT]` (drop the underscore; it's now public)
- `_ThickBarColumn` → `ThickBarColumn`
- `_OptionalMofNCompleteColumn` → `OptionalMofNCompleteColumn`
- `_OptionalETAColumn` → `OptionalETAColumn`

`cli.py` keeps `RichIngestProgress`, `RichQueryProgress`, `_render_ingest_result`, `_render_query_footer`, `_INGEST_PHASE_LABELS`, `_QUERY_PHASE_LABELS` — only the generic base + columns move. Imports in `cli.py` get rewritten to pull from `cli_progress`.

Pros / Cons:
- Pros: `slopmortem/evals/` no longer reaches into `cli.py`'s private API; the abstraction has a name that matches its scope; `cli.py` shrinks by ~150 lines.
- Cons: pure code-motion churn touches a busy file.

Why this and not "leave it private and import the underscore name": three consumers (ingest, query, recording) is the moment to drop the underscore. Cross-module access to `_`-prefixed symbols is the smell we're avoiding.

**Step 2 — Wire Rich into both recorders.**

*Corpus recorder.* `corpus_recorder._record()` already calls `ingest(...)` which accepts `progress=`. Pass `RichIngestProgress()` (TTY-gated). Replace the closing `print(f"wrote {out_path} ...")` with a Rich panel summarizing the dump (output path, byte size, IngestResult counters from the run).

*Eval recorder.* This is the larger half:

1. New `slopmortem/evals/recording_progress.py`:
   - `RecordPhase(StrEnum)`: `ROWS`, `FACET_EXTRACT`, `RERANK`, `SYNTHESIZE`, `EMBED`, `COST`. The first five mirror what `record_cassettes_for_inputs` actually drives; `COST` is a synthetic phase that surfaces the spend ceiling.
   - `RecordProgress(Protocol)` matches `QueryProgress`'s surface (`start_phase`, `advance_phase`, `end_phase`, `set_phase_status`, `log`, `error`) — note `set_phase_status` is on `QueryProgress` only, not on `IngestProgress`. Adds one new method: `cost_update(spent_usd: float, max_usd: float)`.
   - `NullRecordProgress` no-op fallback.

2. `record_cassettes_for_inputs()` grows `progress: RecordProgress | None = None`. Calls `progress.start_phase(ROWS, total=len(inputs))` upfront, advances after each row, calls `progress.log()` with the per-row outcome (`✓ <scope> — N cassettes, $X.XXXX`), and forwards a `_QueryProgressBridge` to inner `run_query(progress=...)` so each row's facet/rerank/synthesize/embed events flow into the same display. The bridge translates `QueryPhase.*` → `RecordPhase.*` and resets the inner tasks at the start of each row.

3. Cost surface: `RecordingLLMClient` already tracks `self._spent_usd` and has `result.cost_usd` from each completion. `RecordingEmbeddingClient` reads `result.cost_usd` from `EmbeddingResult` but does not track a running spend (no `self._spent_usd`); aggregation lives in `record_cassettes_for_inputs`, so the wrapper just emits per-call deltas. Add an `on_cost: Callable[[float], None] | None` parameter to both wrappers; when set, they call it after each successful inner call with the per-call USD delta. `record_cassettes_for_inputs` wires this into `progress.cost_update(...)` against the running total. Note: under the default `embedding_provider="fastembed"` the embedding `cost_usd` is always `0.0`, so the COST bar advances only from LLM calls in the default config — non-zero embedding cost only shows up under `embedding_provider="openai"`. This is correct behavior, not a bug to flag during review.

4. New `slopmortem/evals/render.py`:
   - `RichRecordProgress(RichPhaseProgress[RecordPhase])` — concrete impl. Implements `cost_update` by setting `total=max_usd, completed=spent_usd` on the COST phase task. The bar fills as spend accrues.
   - `_render_record_footer(console, *, total_cost_usd, max_cost_usd, rows_total, rows_succeeded, cassettes_written)` — Rich panel mirroring `_render_query_footer`'s shape.

5. `slopmortem/evals/runner.py::_run_record()` constructs a `RichRecordProgress` only when `sys.stderr.isatty()`, else `NullRecordProgress`. Passes through to `record_cassettes_for_inputs`. After the await, prints the footer panel.

Pros / Cons:
- Pros: matches ingest/query 1:1; the operator sees row counter + per-stage bars + cost meter live; the cost-as-phase-bar makes the spend ceiling visceral without inventing a second render primitive.
- Cons: cost-as-phase-bar is a small abuse of the phase abstraction — the COST "phase" never starts or ends in the pipeline-event sense, it's just a bar that mutates. Acceptable trade because it keeps the entire surface inside one `Progress` widget.

### Why not extract the per-row inner display into a separate `Live`?

Two reasons. First, a single `Progress` already supports multiple tasks; resetting them between rows is cheap. Second, two stacked `Live` renders fight for stdout state and produce flicker. Same widget = same render frame.

### Where the cost-update plumbing lives

`RecordingLLMClient.complete()` already returns a `CompletionResult` with `cost_usd`. Adding the `on_cost` hook there is one extra `if self._on_cost: self._on_cost(result.cost_usd)` after the existing `self._spent_usd += result.cost_usd`. `RecordingEmbeddingClient.embed()` similarly forwards `result.cost_usd` (which is currently 0 for fastembed but non-zero for OpenAI embeddings; the hook handles both). The CLI seam aggregates and pushes to `progress.cost_update(running_total, max_cost_usd)`.

## Tests

Mirror the existing pattern (`tests/test_cli_smoke.py` covers Rich output for ingest):

- `tests/test_cli_progress.py` (new): smoke test that `RichPhaseProgress[FakeEnum]` advances and ends without raising, using `Console(file=StringIO(), force_terminal=True)`.
- `tests/evals/test_recording_progress.py` (new): three checks. (1) `RichRecordProgress` smoke: phase tasks advance/end without raising. (2) Cost-bar correctness: after `cost_update(0.50, 2.00)`, the COST task lands with `total=2.00, completed=0.50`. (3) `NullRecordProgress` no-op assertion (every method returns `None` without side effects).
- `tests/evals/test_recording_helper.py` (extend if it exists, else add): verify `record_cassettes_for_inputs(progress=NullRecordProgress())` runs unchanged, and that a fake `RecordProgress` collects expected events under `RUN_LIVE`-gated cassettes.
- TTY fallback coverage: extend `tests/test_cli_smoke.py` (or add to `test_cli_progress.py`) to assert that with `sys.stderr.isatty()` returning `False`, `corpus_recorder._record()` and `runner._run_record()` both pick the `nullcontext` / `NullRecordProgress` branch instead of trying to drive a Live render.

No new live-cost tests. The existing `--record` cassette flow already exercises the path.

## Layout summary

```
slopmortem/
  cli.py                              # MODIFY: drop _RichPhaseProgress, import from cli_progress
  cli_progress.py                     # NEW: RichPhaseProgress + column helpers
  evals/
    corpus_recorder.py                # MODIFY: wire RichIngestProgress, swap print → panel
    recording.py                      # MODIFY: add on_cost hook to LLM + embedding wrappers
    recording_helper.py               # MODIFY: add progress: RecordProgress | None param
    recording_progress.py             # NEW: RecordPhase, RecordProgress, NullRecordProgress
    render.py                         # NEW: RichRecordProgress, _render_record_footer
    runner.py                         # MODIFY: build RichRecordProgress in _run_record
tests/
  test_cli_progress.py                # NEW
  test_cli_smoke.py                   # MODIFY (or fold into test_cli_progress.py): TTY-fallback assertions
  evals/
    test_recording_progress.py        # NEW
    test_recording_helper.py          # MODIFY (or NEW if absent): NullRecordProgress + fake RecordProgress events
```

## Execution Strategy

**Subagents, sequential.** Two vertical slices, each delivering a demoable end-to-end behavior. Per the user's standing preference, tasks run one at a time even though the executor could theoretically batch independent work. Task B depends on Task A's extracted module, so the dependency graph forces sequencing regardless.

## Task Dependency Graph

| Task | Slice | Predecessor | HITL/AFK |
|------|-------|-------------|----------|
| A | Promote `RichPhaseProgress` to `cli_progress.py`; wire `RichIngestProgress` into corpus recorder; replace closing `print` with Rich summary panel. End-to-end demo: `just eval-record-corpus` shows a Live phase progress display and ends with a summary panel. | none | AFK |
| B | Add `RecordPhase` / `RecordProgress` / `NullRecordProgress` / `RichRecordProgress` / `_render_record_footer`; thread `on_cost` through recording wrappers; plumb `progress=` through `record_cassettes_for_inputs`; wire into `runner._run_record`. End-to-end demo: `just eval-record` shows a Live row counter + per-stage bars + cost meter, and ends with a summary panel. | A | AFK |

## Agent Assignments

| Task | Agent type | Domain |
|------|------------|--------|
| A | python-development:python-pro | Python (CLI / Rich) |
| B | python-development:python-pro | Python (eval infra / Rich) |
| Polish | python-development:python-pro | Uniform Python diff |

## Out of scope

- Replacing the post-run PASS/FAIL line emitter in `runner.main()` for the non-record path. That's a separate UX concern and would broaden the diff.
- Persisting cost-by-row beyond the live display. The runner already records cassettes; cost telemetry isn't a deliverable here.
- Changing pipeline-side `IngestProgress` / `QueryProgress` Protocols. The recording UI consumes them as-is.
