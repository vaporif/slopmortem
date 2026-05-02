# Split `ingest()` Function Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or `/team-feature` to implement this plan task-by-task, per the Execution Strategy below. Steps use checkbox (`- [ ]`) syntax — these are **persistent durable state**, not visual decoration. The executor edits the plan file in place: `- [ ]` → `- [x]` the instant a step verifies, before moving on. On resume (new session, crash, takeover), the executor scans existing `- [x]` marks and skips them — these steps are NOT redone. TodoWrite mirrors this state in-session; the plan file is the source of truth across sessions.

**Goal:** Split the 270-line body of `ingest()` (`slopmortem/ingest.py:878-1149`) into four free helpers in the same module, with no behavior change. Existing tests are the contract; the suite must stay green at every commit.

**Architecture:** Pure within-file extraction — no package split. Four helpers extracted in dependency order: `_cache_read_ratio_event` (pure), then `_emit_collected_events` and `_record_error` promoted from closures to module-level free functions taking `result`, then `_classify_entries`, then `_write_entries`. After extraction, `ingest()` becomes ~50 lines of phase orchestration. Style matches `_process_entry` and `_facet_summarize_fanout` — explicit kwargs, no deps bundle, mutate `result` in place. This is intentionally narrower than `2026-05-02-encapsulation-refactor.md` PR 2 (which moves the whole file into a package); doing the within-file split first makes that subsequent package split mechanical because the seams are already named.

**Tech Stack:** Python 3.13+, anyio, basedpyright (strict, `reportAny="error"`), ruff, pytest with `pytest-xdist`, `just`.

## Execution Strategy

**Subagents.** Tasks dispatched to fresh `python-development:python-pro` agents, one at a time. Each task mutates `slopmortem/ingest.py`, so sequential is mandatory — there is no file-ownership story for parallel work here.

Reason: each extraction is small and independently verifiable, but the same file is the only modified surface, and we want a green `just test && just typecheck && just lint` at the end of every task so any regression is bisectable to one commit. The parent owns commits — agents must not run `git add` or `git commit`.

## Task Dependency Graph

- Task 1 [AFK]: depends on `none` → first batch
- Task 2 [AFK]: depends on `Task 1` → sequential
- Task 3 [AFK]: depends on `Task 2` → sequential
- Task 4 [AFK]: depends on `Task 3` → sequential
- Polish [AFK]: depends on `Task 4` → sequential

All five tasks run sequentially. Task 1 (cache-ratio) is independent of the others but kicks first as a low-risk warmup that proves the verify gate is green. Task 2 (de-closure) is a prerequisite for Tasks 3 and 4 because both helpers call `_record_error`. Tasks 3 and 4 each touch a distinct contiguous block in `ingest()` and could in principle run in parallel, but they're sequenced to keep the file's intermediate state coherent and reviewable.

## Agent Assignments

- Task 1: Extract `_cache_read_ratio_event`            → python-development:python-pro
- Task 2: De-closure `_emit_collected_events` + `_record_error` → python-development:python-pro
- Task 3: Extract `_classify_entries`                  → python-development:python-pro
- Task 4: Extract `_write_entries` + clean noqa codes  → python-development:python-pro
- Polish: post-implementation-polish                   → python-development:python-pro

Agents must stay within the CREATE/MODIFY list of their assigned task. No new dependencies, no new test files, no helper expansions beyond what the task specifies.

---

## Pre-flight: Baseline must be green

Before Task 1, the parent (not a subagent) confirms baseline:

```bash
just test && just typecheck && just lint
```

If any of these fails on `main`/current branch before extraction starts, stop — fix the baseline first. A failing baseline makes the per-task verify gate ambiguous.

External imports of `slopmortem.ingest` were audited at plan-write time:

| Module                                | Imports                                                                                  |
|---------------------------------------|------------------------------------------------------------------------------------------|
| `tests/test_ingest_dry_run.py`        | `FakeSlopClassifier`, `InMemoryCorpus`, `ingest`                                         |
| `tests/test_ingest_idempotency.py`    | `FakeSlopClassifier`, `InMemoryCorpus`, `ingest`                                         |
| `tests/test_ingest_orchestration.py`  | `FakeSlopClassifier`, `InMemoryCorpus`, `ingest` + `ingest_module.chunk_markdown` (monkeypatch) |
| `tests/corpus/test_qdrant_store.py`   | `_Point`                                                                                 |
| `slopmortem/cli.py`                   | `INGEST_PHASE_LABELS`, `IngestPhase`, `IngestResult`, `ingest`, `Corpus`, `SlopClassifier`, `FakeSlopClassifier`, `HaikuSlopClassifier` |
| `slopmortem/evals/corpus_recorder.py` | `INGEST_PHASE_LABELS`, `HaikuSlopClassifier`, `IngestPhase`, `ingest`, `Corpus`, `IngestResult` |

None of these imports reference the closures or the loop bodies we're extracting. The new helpers are underscore-prefixed and stay private — no external surface change.

---

## Task 1: Extract `_cache_read_ratio_event`

**Files:**
- Modify: `slopmortem/ingest.py`

The cache-read-ratio probe is currently 8 inline lines inside `ingest()` (around `slopmortem/ingest.py:1069-1077`). Pulling it into a pure helper proves the verify gate works on the smallest possible change before we touch anything load-bearing.

- [ ] **Step 1: Locate the block**

```bash
grep -nE "_CACHE_READ_RATIO_PROBE_N|CACHE_READ_RATIO_LOW" slopmortem/ingest.py
```

Expected: hits at the constants near the top (`_CACHE_READ_RATIO_THRESHOLD`, `_CACHE_READ_RATIO_PROBE_N`), the probe block inside `ingest()`, and the SpanEvent enum in `slopmortem/tracing/events.py`. The probe block is the contiguous set of ~8 lines starting with `probe = [r for r in fanout ...`.

- [ ] **Step 2: Add the helper above `ingest()` and replace the inline block**

Add this new function at module level just before the `@observe` decorator on `ingest()`:

```python
def _cache_read_ratio_event(
    fanout: Sequence[_FanoutResult | Exception],
) -> str | None:
    """Return :attr:`SpanEvent.CACHE_READ_RATIO_LOW` when the first N fan-out
    responses fall below the cache-read-ratio threshold, else ``None``.

    Pure: caller appends the returned event name to ``result.span_events``.
    """
    probe = [r for r in fanout if isinstance(r, _FanoutResult)][:_CACHE_READ_RATIO_PROBE_N]
    if not probe:
        return None
    total_read = sum(r.cache_read for r in probe)
    total_creation = sum(r.cache_creation for r in probe)
    denom = total_read + total_creation
    if denom <= 0:
        return None
    if total_read / denom < _CACHE_READ_RATIO_THRESHOLD:
        return SpanEvent.CACHE_READ_RATIO_LOW.value
    return None
```

Replace the inline probe block inside `ingest()` (currently `slopmortem/ingest.py:1069-1077`) with:

```python
ratio_event = _cache_read_ratio_event(fanout)
if ratio_event is not None:
    result.span_events.append(ratio_event)
```

`Sequence` is already imported under `if TYPE_CHECKING:` — no new imports needed.

- [ ] **Step 3: Verify gate**

```bash
just test && just typecheck && just lint
```

Expected: green. The `test_ingest_cache_read_ratio_warning` test in `tests/test_ingest_orchestration.py:313` exercises this exact path — if extraction broke the ratio math it will fail loudly.

- [ ] **Step 4: Hand off for commit**

Stop. Do not run `git add` or `git commit`. Report success to the parent; the parent commits with subject `refactor: extract _cache_read_ratio_event`.

---

## Task 2: De-closure `_emit_collected_events` and `_record_error`

**Files:**
- Modify: `slopmortem/ingest.py`

Both helpers are currently nested closures inside `ingest()` that capture `result`. Promoting them to module-level free functions taking `result` as the first arg matches the rest of this file (helpers like `_gather_entries` already take `span_events=result.span_events` explicitly rather than closing over `result`). It's also the prerequisite for Tasks 3 and 4 — those helpers need a way to call `_record_error` without smuggling closures through their kwargs.

- [ ] **Step 1: Locate the closures and their three-each call sites**

```bash
grep -nE "_emit_collected_events|_record_error" slopmortem/ingest.py
```

Expected: the two `def` lines inside `ingest()` (around `:932` and `:940`), plus three call sites for each (one inside the classify loop, one in the fanout-failed branch, one in the write-failed branch; emit has three at 1045, 1049, 1148).

- [ ] **Step 2: Add module-level definitions**

Add these two free functions at module level, just below `_record_ingest_error` would make sense — concretely, place them immediately after the `IngestResult` dataclass (around `slopmortem/ingest.py:339`) so they're near the type they operate on:

```python
def _emit_collected_events(result: IngestResult) -> None:
    """Replay every collected span event onto the active Laminar span.

    No-op when Laminar isn't initialized so tests don't need to mock it.
    """
    if not Laminar.is_initialized():
        return
    for name in result.span_events:
        Laminar.event(name=name)


def _record_error(result: IngestResult, entry_label: str, exc: BaseException) -> None:
    """Attach indexed error attributes to the active span until ``_MAX_RECORDED_ERRORS``.

    Past the cap, set ``errors.truncated_count`` once and stop adding keys so a
    pathological run can't blow past Laminar's per-span attribute limit. Reads
    ``result.errors`` as the running index — caller still increments
    ``result.errors`` separately.
    """
    if not Laminar.is_initialized():
        return
    idx = result.errors
    if idx >= _MAX_RECORDED_ERRORS:
        Laminar.set_span_attributes(
            {"errors.truncated_count": idx - _MAX_RECORDED_ERRORS + 1}
        )
        return
    Laminar.set_span_attributes(
        {
            f"errors.{idx}.entry": entry_label,
            f"errors.{idx}.exception_type": type(exc).__name__,
            f"errors.{idx}.message": str(exc)[:500],
        }
    )
```

- [ ] **Step 3: Delete the inner closures and update call sites**

Inside `ingest()`:
- Delete the `def _emit_collected_events()` and `def _record_error(...)` blocks (currently lines ~932-953).
- Update three `_record_error(f"{entry.source}:{entry.source_id}", exc)` calls to `_record_error(result, f"{entry.source}:{entry.source_id}", exc)` (current locations: line 999 in the classify enricher-fail branch; line 1089 in the fanout-fail branch; line 1121 in the write-fail branch).
- Update three `_emit_collected_events()` calls to `_emit_collected_events(result)` (current locations: lines 1045, 1049, 1148).

The closures' Laminar imports (`Laminar.is_initialized`, `Laminar.set_span_attributes`, `Laminar.event`) are already at module level — no new imports.

- [ ] **Step 4: Verify gate**

```bash
just test && just typecheck && just lint
```

Expected: green. `test_ingest_per_source_failure_does_not_abort_run` in `tests/test_ingest_orchestration.py:175` exercises both helpers together (Laminar attribute counters and the deferred event replay) — a regression here surfaces immediately.

- [ ] **Step 5: Hand off for commit**

Stop. Report to parent; commit subject: `refactor: lift _emit_collected_events and _record_error to module scope`.

---

## Task 3: Extract `_classify_entries`

**Files:**
- Modify: `slopmortem/ingest.py`

The classify phase is the larger of the two inline loops — currently around `slopmortem/ingest.py:990-1041`. It enriches, slop-classifies, and quarantines, returning the keepers list and mutating `result` counters.

- [ ] **Step 1: Locate the block**

```bash
grep -nE "progress.start_phase\(IngestPhase.CLASSIFY|progress.end_phase\(IngestPhase.CLASSIFY" slopmortem/ingest.py
```

Capture the start and end line of the contiguous block bounded by these two calls — that's the entire classify phase.

- [ ] **Step 2: Add the helper above `ingest()`**

Add this async function at module level, just above the `@observe` decorator on `ingest()`:

```python
async def _classify_entries(  # noqa: PLR0913 - orchestration density is the contract
    entries: Sequence[RawEntry],
    *,
    enrichers: Sequence[Enricher],
    slop_classifier: SlopClassifier,
    journal: MergeJournal,
    config: Config,
    post_mortems_root: Path,
    dry_run: bool,
    progress: IngestProgress,
    result: IngestResult,
) -> list[tuple[RawEntry, str]]:
    """Enrich, slop-classify, quarantine. Return keepers; mutate ``result`` counters.

    Per-entry isolation:
    - Enricher failure: log + ``result.errors`` + ``INGEST_ENTRY_FAILED`` event, continue.
    - Empty body: ``result.skipped += 1``, continue.
    - Slop classifier failure: log, treat as score=0.0 (entry survives), continue.
    - Slop above threshold: quarantine to disk + journal (skipped under ``dry_run``),
      ``result.quarantined += 1``, continue.

    Pre-vetted sources (curated YAML, Crunchbase CSV) bypass the LLM judge and
    take ``slop_score = 0.0`` directly — see ``_PRE_VETTED_SOURCES``.
    """
    progress.start_phase(IngestPhase.CLASSIFY, total=len(entries))
    keepers: list[tuple[RawEntry, str]] = []
    for entry in entries:
        result.seen += 1
        try:
            enriched = await _enrich_pipeline(entry, enrichers)
        except Exception as exc:  # noqa: BLE001 - per-entry isolation.
            logger.warning("ingest: enricher failed for %r: %s", entry.source_id, exc)
            progress.error(IngestPhase.CLASSIFY, f"enricher failed for {entry.source_id}: {exc}")
            _record_error(result, f"{entry.source}:{entry.source_id}", exc)
            result.errors += 1
            result.span_events.append(SpanEvent.INGEST_ENTRY_FAILED.value)
            progress.advance_phase(IngestPhase.CLASSIFY)
            continue

        body = _entry_summary_text(enriched, max_tokens=config.max_doc_tokens)
        if not body:
            result.skipped += 1
            progress.advance_phase(IngestPhase.CLASSIFY)
            continue

        if entry.source in _PRE_VETTED_SOURCES:
            slop_score = 0.0
        else:
            try:
                slop_score = await slop_classifier.score(body)
            except Exception as exc:  # noqa: BLE001 - defensive: never abort on classifier failure.
                logger.warning("ingest: slop classifier failed: %s", exc)
                progress.error(IngestPhase.CLASSIFY, f"slop classifier failed: {exc}")
                slop_score = 0.0

        if slop_score > config.slop_threshold:
            if not dry_run:
                await _quarantine(
                    journal=journal,
                    entry=enriched,
                    body=body,
                    slop_score=slop_score,
                    post_mortems_root=post_mortems_root,
                )
            result.quarantined += 1
            result.span_events.append(SpanEvent.SLOP_QUARANTINED.value)
            progress.advance_phase(IngestPhase.CLASSIFY)
            continue

        keepers.append((enriched, body))
        progress.advance_phase(IngestPhase.CLASSIFY)
    progress.end_phase(IngestPhase.CLASSIFY)
    return keepers
```

- [ ] **Step 3: Replace the inline classify block in `ingest()`**

Delete the inline block (the entire region from `progress.start_phase(IngestPhase.CLASSIFY ...)` through `progress.end_phase(IngestPhase.CLASSIFY)` and the trailing `progress.log(f"classified: ...")`). Note: the `progress.log` line stays — only the loop body gets replaced.

Replace with:

```python
keepers = await _classify_entries(
    entries,
    enrichers=enrichers,
    slop_classifier=slop_classifier,
    journal=journal,
    config=config,
    post_mortems_root=post_mortems_root,
    dry_run=dry_run,
    progress=progress,
    result=result,
)
quarantined = result.quarantined
skipped = result.skipped
progress.log(f"classified: {len(keepers)} kept, {quarantined} quarantined, {skipped} skipped")
```

The two locals (`quarantined`, `skipped`) are read directly off `result` post-call so the log line keeps its existing wording. They're not strictly needed if you inline them into the f-string; either is fine — match what passes lint.

- [ ] **Step 4: Verify gate**

```bash
just test && just typecheck && just lint
```

Expected: green. Tests covering this path: `test_ingest_quarantines_slop` (`:350`), `test_ingest_per_source_failure_does_not_abort_run` (`:175`), and the dry-run suite in `tests/test_ingest_dry_run.py`.

- [ ] **Step 5: Hand off for commit**

Stop. Report to parent; commit subject: `refactor: extract _classify_entries`.

---

## Task 4: Extract `_write_entries` and clean stale noqa codes

**Files:**
- Modify: `slopmortem/ingest.py`

The write-phase loop is currently around `slopmortem/ingest.py:1079-1146`. It pairs each keeper with its fan-out result, dispatches to `_process_entry`, isolates per-entry failures, and surfaces fatal `BaseException`s loudly. After this extraction `ingest()` itself shrinks enough that several `noqa` codes on its signature stop being justified — drop the unjustified ones in this same task.

- [ ] **Step 1: Locate the block**

```bash
grep -nE "progress.start_phase\(IngestPhase.WRITE|progress.end_phase\(IngestPhase.WRITE" slopmortem/ingest.py
```

Capture the contiguous block bounded by these two calls — the entire write phase.

- [ ] **Step 2: Add the helper above `ingest()`**

Add this async function at module level, just above the `@observe` decorator on `ingest()` (and below the `_classify_entries` helper added in Task 3):

```python
async def _write_entries(  # noqa: PLR0913 - orchestration density is the contract
    keepers: Sequence[tuple[RawEntry, str]],
    fanout: Sequence[_FanoutResult | Exception],
    *,
    journal: MergeJournal,
    corpus: Corpus,
    embed_client: EmbeddingClient,
    llm: LLMClient,
    config: Config,
    post_mortems_root: Path,
    force: bool,
    sparse_encoder: SparseEncoder,
    progress: IngestProgress,
    result: IngestResult,
) -> None:
    """Write phase: pair keepers with fan-out results and process each.

    Walks ``keepers`` and ``fanout`` in lockstep with ``strict=True``. Per-entry
    failures from the fan-out (``Exception`` payloads) and from
    :func:`_process_entry` itself are isolated onto ``result``. Fatal
    ``BaseException`` (CancelledError, SystemExit) is surfaced via
    ``progress.error`` and re-raised so the run terminates loud, not silent.
    """
    progress.start_phase(IngestPhase.WRITE, total=len(keepers))
    for (entry, body), fan in zip(keepers, fanout, strict=True):
        if isinstance(fan, BaseException):
            logger.warning(
                "ingest: fan-out failed for %s:%s: %s", entry.source, entry.source_id, fan
            )
            progress.error(
                IngestPhase.FAN_OUT,
                f"fan-out failed for {entry.source}:{entry.source_id}: {fan}",
            )
            _record_error(result, f"{entry.source}:{entry.source_id}", fan)
            result.errors += 1
            result.span_events.append(SpanEvent.INGEST_ENTRY_FAILED.value)
            progress.advance_phase(IngestPhase.WRITE)
            continue
        try:
            outcome = await _process_entry(
                entry,
                body=body,
                fan=fan,
                journal=journal,
                corpus=corpus,
                embed_client=embed_client,
                llm=llm,
                config=config,
                post_mortems_root=post_mortems_root,
                slop_score=0.0,  # we already filtered slop above
                force=force,
                span_events=result.span_events,
                sparse_encoder=sparse_encoder,
            )
        except Exception as exc:  # noqa: BLE001 - per-entry isolation; run continues.
            logger.warning(
                "ingest: write phase failed for %s:%s: %s",
                entry.source,
                entry.source_id,
                exc,
            )
            progress.error(
                IngestPhase.WRITE,
                f"write phase failed for {entry.source}:{entry.source_id}: {exc}",
            )
            _record_error(result, f"{entry.source}:{entry.source_id}", exc)
            result.errors += 1
            result.span_events.append(SpanEvent.INGEST_ENTRY_FAILED.value)
            progress.advance_phase(IngestPhase.WRITE)
            continue
        except BaseException as exc:
            # CancelledError / SystemExit / etc; ``except Exception`` above misses
            # these. Surface what's escaping (which entry, what type) via the
            # progress error channel before letting it propagate, so the run
            # terminates loud rather than silent.
            progress.error(
                IngestPhase.WRITE,
                f"FATAL {type(exc).__name__} on {entry.source}:{entry.source_id}: {exc}",
            )
            raise
        match outcome:
            case ProcessOutcome.PROCESSED:
                result.processed += 1
            case ProcessOutcome.SKIPPED:
                result.skipped += 1
            case ProcessOutcome.SKIPPED_EMPTY:
                result.skipped_empty += 1
            case ProcessOutcome.FAILED:
                result.failed += 1
        progress.advance_phase(IngestPhase.WRITE)
    progress.end_phase(IngestPhase.WRITE)
```

- [ ] **Step 3: Replace the inline write block in `ingest()`**

Delete the inline block (the entire region from `progress.start_phase(IngestPhase.WRITE ...)` through `progress.end_phase(IngestPhase.WRITE)`).

Replace with:

```python
await _write_entries(
    keepers,
    fanout,
    journal=journal,
    corpus=corpus,
    embed_client=embed_client,
    llm=llm,
    config=config,
    post_mortems_root=post_mortems_root,
    force=force,
    sparse_encoder=sparse_encoder,
    progress=progress,
    result=result,
)
```

- [ ] **Step 4: Trim stale noqa codes on `ingest()`**

`ingest()` currently has `# noqa: PLR0913, C901, PLR0912, PLR0915` on its signature. After the extraction, the body should be ~50 statements with shallow branching. Try removing `C901, PLR0912, PLR0915` and keep only `PLR0913`:

```python
async def ingest(  # noqa: PLR0913 - orchestration takes every dependency.
```

If `just lint` complains after the trim, restore the specific code(s) ruff still flags. Don't restore a code ruff isn't asking for — the goal is to keep noqa scoped to what's actually needed.

- [ ] **Step 5: Verify gate**

```bash
just test && just typecheck && just lint
```

Expected: green. Tests exercising the write phase: `test_ingest_wires_summary_into_payload` (`:144`), `test_ingest_zero_chunks_skips_mark_complete` (`:469`), `test_delete_failure_aborts_entry_marked_failed` (`:527`), and the idempotency suite in `tests/test_ingest_idempotency.py`.

- [ ] **Step 6: Hand off for commit**

Stop. Report to parent; commit subject: `refactor: extract _write_entries`.

---

## Polish

After Task 4 lands, dispatch a single `python-development:python-pro` subagent with the `superpowers:post-implementation-polish` skill scope: review the four extracted helpers and the trimmed `ingest()` body for AI-flavored comments, dead variables, redundant log lines, and any noqa codes that became unnecessary mid-task. Constraint: the polish pass MUST NOT change behavior — no test edits, no signature changes on `ingest()` or `_process_entry`, and no new helpers. If the polish agent finds something that warrants a behavior change, it reports it back rather than implementing it.

Verify gate after polish: `just test && just typecheck && just lint`. Commit subject: `cleanup: post-extract polish`.

---

## What we explicitly are NOT doing

- **Not splitting `ingest.py` into a package.** That's `2026-05-02-encapsulation-refactor.md` PR 2 and is gated by a `~$2` cassette regeneration. This plan is the within-file precursor and complements it.
- **Not introducing a `_IngestDeps` dataclass.** No other module in `slopmortem/` bundles deps that way; doing so unilaterally introduces a pattern the codebase doesn't have.
- **Not converting `_emit_collected_events` / `_record_error` into methods on `IngestResult`.** That dataclass is pure data for CLI/JSON rendering — coupling it to Laminar muddies the role.
- **Not adding new tests.** This is a behavior-preserving refactor; the existing suite (`tests/test_ingest_*.py`) is the contract.
- **Not bumping pinned models or touching cassettes.** Per CLAUDE.md, those bumps drift the eval baseline.

---

## Self-review

**Spec coverage:**
- `_cache_read_ratio_event` extraction → Task 1.
- `_emit_collected_events` and `_record_error` de-closure → Task 2.
- `_classify_entries` extraction → Task 3.
- `_write_entries` extraction + noqa cleanup → Task 4.
- Polish pass → final task per the skill's mandatory Polish line.

All four extractions discussed in conversation are covered. Nothing extra crept in.

**Placeholder scan:** No "TBD", no "implement later", no "similar to Task N", no naked "add error handling" instructions. Each step shows exact code or exact `grep` invocation.

**Type consistency:** `_FanoutResult`, `IngestResult`, `Sequence`, `Enricher`, `SlopClassifier`, `Corpus`, `LLMClient`, `EmbeddingClient`, `IngestProgress`, `Config`, `RawEntry`, `MergeJournal`, `Path`, `SparseEncoder`, `_record_error(result, ...)`, `_emit_collected_events(result)` — names used in Tasks 3 and 4 match those defined or imported earlier in the file (or in Task 2 for the de-closured pair).

**Risk audit:**
- Closures capture `result`; free functions take `result` explicitly. Both `Laminar.event` and `Laminar.set_span_attributes` operate on the active span context, and the call sites stay inside `ingest()` (which holds the `@observe(name="ingest")` span). Behavior preserved.
- `_record_error`'s mutation of "running index" is the read of `result.errors` (caller still does the `+= 1`). Same as before — confirmed by inspection of the pre-extract closure body.
- `_write_entries` keeps the inner `except BaseException` re-raise. CancelledError still propagates loud.
- `progress.log("classified: ...")` stays in `ingest()` after Task 3 so test suites that observe progress output in any specific order are unaffected.
- No external imports of `_emit_collected_events` / `_record_error` / new helpers — verified at plan-write time via repo-wide grep.
