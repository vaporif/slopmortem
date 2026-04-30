# Query Progress UI — Design

Date: 2026-04-30
Topic: bring `slopmortem query` progress display to parity with `slopmortem ingest`
Owner: Dmytro Onypko

## Context

`slopmortem ingest` ships a Rich-based progress display (`RichIngestProgress` in `slopmortem/cli.py:714`) with a per-phase spinner, bar, M/N counter, elapsed timer, ETA, error count badge, and a final summary table. The query path has nothing comparable: `_make_progress` (`slopmortem/cli.py:688`) just hands `run_query` a `Callable[[str], None]` that prints `slopmortem: <stage>` lines to stderr. No spinner, no bar, no timing, no costs, no per-candidate visibility on the synthesize fan-out.

Goal: a Rich progress UI on the query side that mirrors the ingest visual language, with a real M/N progress bar on the synthesize stage (the only multi-item phase) and a one-line cost/latency footer at the end.

Out of scope: refactoring `RichIngestProgress` and the new `RichQueryProgress` to share a base. They are structurally similar and a future consolidation makes sense, but it doesn't serve this task.

## Architecture

### Pipeline-side surface (in `slopmortem/pipeline.py`)

Add a closed-set phase enum and a Protocol-shaped progress sink, both modeled directly on the ingest equivalents:

```python
class QueryPhase(StrEnum):
    FACET_EXTRACT = "facet_extract"
    RETRIEVE = "retrieve"
    RERANK = "rerank"
    SYNTHESIZE = "synthesize"

@runtime_checkable
class QueryProgress(Protocol):
    def start_phase(self, phase: QueryPhase, total: int) -> None: ...
    def advance_phase(self, phase: QueryPhase, n: int = 1) -> None: ...
    def end_phase(self, phase: QueryPhase) -> None: ...
    def log(self, message: str) -> None: ...
    def error(self, phase: QueryPhase, message: str) -> None: ...

class NullQueryProgress:  # no-op default for tests / piped runs
    ...
```

Replace the existing `progress: Callable[[str], None] | None` parameter on `run_query` with `progress: QueryProgress | None = None`. The ignore list on `@observe` already excludes `progress`, so Laminar wiring needs no change.

Per-stage emissions inside `run_query`:

| Phase            | total | emissions                                                                |
|------------------|-------|--------------------------------------------------------------------------|
| FACET_EXTRACT    | 1     | start, run, end                                                          |
| RETRIEVE         | 1     | start, run, end                                                          |
| RERANK           | 1     | start, run, end                                                          |
| SYNTHESIZE       | N     | start(total=len(top_n)), advance(1) per candidate finish, end            |

Per-candidate exceptions inside synthesize also call `progress.error(SYNTHESIZE, str(exc))` so the bar shows a red error count badge — same UX as ingest. The pipeline still drops exceptions silently from `Report.candidates`; the visible signal is purely on the progress display.

### Threading per-candidate progress through `synthesize_all`

`synthesize_all` (`slopmortem/stages/synthesize.py:141`) currently fans out via `gather_resilient` and returns a flat list. Add an optional callback:

```python
async def synthesize_all(
    candidates, ctx, llm, config,
    *,
    model=None, max_tokens=None,
    on_candidate_done: Callable[[BaseException | None], None] | None = None,
) -> list[Synthesis | BaseException]:
```

Wrap each `synthesize` call (warm + fan-out) in a small inner coroutine that invokes `on_candidate_done(None)` on success or `on_candidate_done(exc)` on exception, then returns the original value/exception so `gather_resilient`'s contract is preserved. The callback fires from inside the wrapper, so per-candidate ordering matches actual completion order, not list order — which is what we want for a live bar.

### CLI-side `RichQueryProgress` (in `slopmortem/cli.py`)

Implements `QueryProgress` over a `rich.progress.Progress`. Same column set as `RichIngestProgress`:

```
SpinnerColumn,
TextColumn("{task.description}", justify="left"),
BarColumn(...),
MofNCompleteColumn,
TextColumn("[dim]•"),
TimeElapsedColumn,
TextColumn("[dim]eta"),
TimeRemainingColumn,
```

Phase labels:

| Phase            | Label                          |
|------------------|--------------------------------|
| FACET_EXTRACT    | "Extracting facets"            |
| RETRIEVE         | "Retrieving candidates"        |
| RERANK           | "Reranking candidates"         |
| SYNTHESIZE       | "Synthesizing post-mortems"    |

Tasks created lazily on first `start_phase` so a phase that's never reached doesn't render an empty bar. Error counts displayed as a red `(N error[s])` suffix on the description, matching `_label` in `RichIngestProgress`.

### CLI wire-up (`_query` and `_replay`)

Same TTY gate and same exception wrapper that ingest uses (`slopmortem/cli.py:304-342`). Build the progress context the same way:

```python
progress_ctx = RichQueryProgress() if sys.stderr.isatty() else contextlib.nullcontext()
err_console = Console(stderr=True)
try:
    with progress_ctx as bar:
        report = await run_query(..., progress=bar)
except KeyboardInterrupt:
    err_console.rule("[bold yellow]query cancelled (Ctrl-C)", style="yellow")
    raise
except BaseException:
    err_console.rule("[bold red]query failed", style="red")
    err_console.print_exception(show_locals=False)
    raise
```

After the run, print a one-line footer to stderr (only when `bar is not None`):

```
done • cost=$0.0123 • latency=842ms • synthesized=4/5 • trace=abc123
```

`trace=` is omitted when `report.pipeline_meta.trace_id is None`. `synthesized=K/N` shows `len(report.candidates)` over `config.N_synthesize`.

Drop `_make_progress` once both `_query` and `_replay` are migrated.

`_replay` runs `run_query` per row. Use a single shared `RichQueryProgress` across all rows — the lazy-task pattern means each row's bars are reset (via `Progress.reset`) on the next `start_phase` call. This matches how the ingest display would behave if invoked multiple times in one process.

### Pros / cons

Pros:
- Visual parity with ingest; users get the familiar progress UI on both commands.
- Real M/N bar on synthesize, the only multi-item stage and where most query latency lives.
- Per-candidate failures become visible on the bar instead of silently disappearing into the dropped-from-`Report` set.
- Protocol-shaped sink mirrors `IngestProgress`, leaving room for a future shared base without paying the refactor cost now.

Cons:
- `run_query` signature breaks the simple `Callable[[str], None]` contract; one e2e test updates from `list.append` to a small recording stub. Acceptable — the test was checking that progress was called, which the stub still verifies.
- Single-shot stages (facet_extract, retrieve, rerank) render a 0→1 bar that's visually redundant. Mitigated: the spinner + description still carry signal, and matching the ingest column set is more valuable than per-stage layout customization.
- ~80 lines of `RichQueryProgress` code that's structurally near-duplicate of `RichIngestProgress`. Acknowledged; refactoring into a shared base is out of scope.

## Files

MODIFY:
- `slopmortem/pipeline.py` — add `QueryPhase` / `QueryProgress` / `NullQueryProgress`; change `run_query`'s `progress` parameter type; emit `start_phase` / `advance_phase` / `end_phase` / `error` per stage; pass `on_candidate_done` into `synthesize_all`.
- `slopmortem/stages/synthesize.py` — add `on_candidate_done: Callable[[BaseException | None], None] | None = None` kwarg to `synthesize_all`; wrap each `synthesize` call so it fires the callback on success or exception.
- `slopmortem/cli.py` — add `RichQueryProgress` mirroring `RichIngestProgress`; wire it into `_query` and `_replay` with the TTY gate and exception wrapper; print a one-line footer pulled from `report.pipeline_meta`; drop `_make_progress`.
- `tests/test_pipeline_e2e.py` — replace the `progress_events.append` callable with a small recording stub that implements `QueryProgress` and asserts the same set of phases were touched.

CREATE: none.

## Testing

- Existing `tests/test_pipeline_e2e.py` continues to assert that progress is exercised; only the recording shape changes.
- Existing `tests/stages/test_synthesize.py` unchanged — `on_candidate_done` defaults to `None` so the synthesize-side test surface is unaffected.
- Manual smoke: run `slopmortem query "<some pitch>"` against a populated corpus and visually confirm the four bars render with the synthesize bar advancing through `N_synthesize` candidates.

## Execution Strategy

**Parallel subagents** (default). The work is 4 narrow file-scoped tasks with clear ownership boundaries and no inter-task coordination beyond the `QueryProgress` Protocol shape, which is fully specified in this document. Per-task review is sufficient — there is no cross-stream concern that would require persistent agents with messaging.

Per user preference recorded in memory ("Sequential plan execution preferred"), the subagents are dispatched one at a time rather than concurrently. The strategy itself is still parallel subagents — the dispatch cadence is what changes.

## Agent Assignments

| Task | Description | Agent | Language/Domain |
|------|-------------|-------|-----------------|
| 1 | `pipeline.py` — `QueryPhase`, `QueryProgress`, `NullQueryProgress`, signature change, per-stage emissions, thread `on_candidate_done` | `python-development:python-pro` | Python |
| 2 | `synthesize.py` — add `on_candidate_done` kwarg to `synthesize_all`; wrap warm + fan-out calls | `python-development:python-pro` | Python |
| 3 | `cli.py` — `RichQueryProgress`, wire into `_query` and `_replay`, footer, drop `_make_progress` | `python-development:python-pro` | Python |
| 4 | `test_pipeline_e2e.py` — replace callable progress stub with `QueryProgress`-shaped recorder | `python-development:python-pro` | Python |

Tasks 1 → 2 → 3 → 4 in order. Task 1 publishes the Protocol; task 2 adds the synthesize-side hook task 1 depends on for `on_candidate_done` threading (task 1 is implemented against the agreed kwarg name `on_candidate_done` so task 2 only needs to expose it); task 3 consumes the Protocol from task 1; task 4 updates the test against the Protocol from task 1.
