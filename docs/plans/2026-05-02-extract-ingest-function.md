# Extract `ingest()` to `_ingest.py` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or `/team-feature` to implement this plan task-by-task, per the Execution Strategy below. Steps use checkbox (`- [ ]`) syntax — these are **persistent durable state**, not visual decoration. The executor edits the plan file in place: `- [ ]` → `- [x]` the instant a step verifies, before moving on. On resume (new session, crash, takeover), the executor scans existing `- [x]` marks and skips them — these steps are NOT redone. TodoWrite mirrors this state in-session; the plan file is the source of truth across sessions.

**Goal:** Move the `ingest()` function out of `_orchestrator.py` into a dedicated `_ingest.py` so the package's dependency graph becomes a clean DAG and the two `# noqa: E402` runtime-circular-import blocks die.

**Architecture:** `_orchestrator.py` keeps the types, protocols, dataclasses, and pure helpers — it stops being an orchestrator at all. `_ingest.py` holds only the public `ingest()` function and its closures. Helper modules (`_warm_cache`, `_fan_out`, `_journal_writes`, `_slop_gate`) keep importing types/helpers from `_orchestrator` as before; `_ingest.py` imports from all of them top-of-file. `_orchestrator.py` no longer imports back from `_journal_writes` or `_fan_out`, which is what kills the cycle.

**Tech Stack:** Python 3.13+, anyio, basedpyright (strict), ruff, import-linter, just, pytest with cassettes.

## Execution Strategy

**Subagents.** Default — no spec override. Sequential per the user's standing preference for one-task-at-a-time execution. The work is a single mechanical extraction with one verification gate, so there's no parallelism to exploit anyway.

## Task Dependency Graph

- T1 [AFK]: depends on `none` → first batch (only task)

Single-task plan. One batch, one dispatch.

## Agent Assignments

- T1 — Move `ingest()` to `_ingest.py` → python-development:python-pro
- Polish: post-implementation-polish → general-purpose

**Subagent brief boilerplate:** "Do not stage, do not commit, stay strictly within the CREATE/MODIFY file list for this task. Parent session owns commit authorship."

---

## Layout target

```
slopmortem/ingest/
  __init__.py          # re-export `ingest` from _ingest instead of _orchestrator
  _ingest.py           # NEW — only the `ingest()` function and its closures
  _orchestrator.py     # types, protocols, dataclasses, pure helpers (E402 blocks removed)
  _warm_cache.py       # unchanged
  _fan_out.py          # unchanged
  _journal_writes.py   # unchanged
  _slop_gate.py        # unchanged
```

## Pros / cons of this shape

**Pros:**
- Acyclic dependency graph: `_ingest → {_journal_writes, _fan_out, _warm_cache, _slop_gate} → _orchestrator`. The `# noqa: E402` blocks at `_orchestrator.py:446` and `:450` go away.
- One file, one responsibility: `_ingest.py` is the orchestration function; `_orchestrator.py` is the type-and-helper grab-bag.
- Mechanical change with zero behavior delta — extraction, not redesign.

**Cons:**
- `_orchestrator.py` becomes a misnomer once `ingest()` leaves. Renaming is out of scope here (one rename = one PR's worth of git churn); flagged for follow-up.
- Adds one file to the package. Already at 6, going to 7.

---

## Task T1: Move `ingest()` into `_ingest.py`

**Files:**
- Create: `slopmortem/ingest/_ingest.py`
- Modify: `slopmortem/ingest/_orchestrator.py`
- Modify: `slopmortem/ingest/__init__.py`

The `ingest()` function lives at `slopmortem/ingest/_orchestrator.py:471-711` (verify on first read; line numbers can drift). It owns two local closures (`_emit_collected_events`, `_record_error`) that move with it. Nothing else moves.

- [x] **Step 1: Verify the function's line range and import surface**

Run:

```bash
grep -nE "^async def ingest|^@observe" slopmortem/ingest/_orchestrator.py
grep -nE "noqa: E402" slopmortem/ingest/_orchestrator.py
```

Expected: `@observe` at ~line 455, `async def ingest` at ~line 471, two E402 lines at ~446 and ~450. If line numbers diverge, use the actual values for subsequent steps.

- [x] **Step 2: Inventory what `ingest()` reads from `_orchestrator.py`**

Read the function body (lines 471-711) and list every name it references that is defined in `_orchestrator.py` (not imported). Expected set:

- Types / protocols / dataclasses: `Corpus`, `IngestResult`, `IngestPhase`, `INGEST_PHASE_LABELS`, `NullProgress`, `IngestProgress`, `SlopClassifier`, `SparseEncoder`, `_Point`, `FakeSlopClassifier`, `HaikuSlopClassifier`, `InMemoryCorpus` (for type checks only — verify which are actually referenced)
- Constants: `_MAX_RECORDED_ERRORS`
- Helpers: `_entry_summary_text`, `_enrich_pipeline`, `_gather_entries`, `_truncate_to_tokens`, `_text_id_for`, `_reliability_for`, `_skip_key`, `_build_payload`, `_content_sha256`, `_date_from_year`

Confirm by greping the function body for each name. The list above is the import surface `_ingest.py` will need from `_orchestrator`.

- [x] **Step 3: Create `slopmortem/ingest/_ingest.py` with the function moved**

```python
# pyright: reportAny=false
"""Ingest orchestration entry point.

This module holds only the `ingest()` function. Types, protocols, dataclasses,
and pure helpers live in `_orchestrator.py`; per-stage logic lives in
`_warm_cache.py`, `_fan_out.py`, `_journal_writes.py`, and `_slop_gate.py`.
The split keeps the package's dependency graph acyclic.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lmnr import observe

from slopmortem.ingest._fan_out import _facet_summarize_fanout
from slopmortem.ingest._journal_writes import ProcessOutcome, _process_entry
from slopmortem.ingest._orchestrator import (
    INGEST_PHASE_LABELS,
    IngestPhase,
    IngestResult,
    NullProgress,
    # plus every name from Step 2's inventory
)
from slopmortem.ingest._slop_gate import _quarantine, classify_one
from slopmortem.ingest._warm_cache import cache_read_ratio_event, cache_warm
from slopmortem.tracing import SpanEvent

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from slopmortem.budget import Budget
    from slopmortem.config import Config
    from slopmortem.corpus import MergeJournal
    from slopmortem.corpus.sources import Enricher, Source
    from slopmortem.llm import EmbeddingClient, LLMClient

__all__ = ["ingest"]

logger = logging.getLogger(__name__)


@observe(
    name="ingest",
    ignore_inputs=[
        "sources",
        "enrichers",
        "journal",
        "corpus",
        "llm",
        "embed_client",
        "budget",
        "slop_classifier",
        "sparse_encoder",
        "progress",
    ],
)
async def ingest(  # noqa: PLR0913, C901, PLR0912, PLR0915 - orchestration takes every dependency.
    # ... existing signature, body, and closures verbatim ...
) -> IngestResult:
    ...
```

Cut-and-paste the function (signature + decorator + docstring + body + both closures) from `_orchestrator.py`. Do not rewrite. `# pyright: reportAny=false` at the top matches the existing private-module pragma pattern (`_journal_writes.py:1`, `_orchestrator.py:1`).

The `pyright: ignore[reportPrivateUsage]` pragmas the function used inline (e.g. on `_text_id_for`, `_skip_key`, `_build_payload`) need to come along — those calls cross a private boundary now that they're called from a different module. Match the pragma pattern in `_journal_writes.py:40-46`.

- [x] **Step 4: Delete `ingest()` and the late-import blocks from `_orchestrator.py`**

Remove:
- The entire `async def ingest(...)` body (lines 471-711, plus the `@observe(...)` decorator at lines 455-470)
- The two `# noqa: E402` import blocks at lines 446-449 and 450-453 — they exist only because `ingest()` needed `_facet_summarize_fanout`, `_FanoutResult`, `ProcessOutcome`, `_process_entry`. After the move, nothing in `_orchestrator.py` references those names.
- Any imports at the top of `_orchestrator.py` that are now unused (`Laminar`, `observe`, `git_sha`, `mint_run_id`, `cache_read_ratio_event`, `cache_warm`, `_quarantine`, `classify_one`, etc.). `ruff check --select F401` will surface every dead import; fix them all.

Keep everything else: types, protocols, dataclasses, pure helpers, the `_emit_collected_events` and `_record_error` patterns are inside `ingest()` and travel with it.

- [x] **Step 5: Update `slopmortem/ingest/__init__.py` to re-export from `_ingest`**

Edit `__init__.py` line 32 (the existing `ingest as ingest` import line) to source from `_ingest`:

```python
from slopmortem.ingest._ingest import (
    ingest as ingest,
)
```

Other re-exports (types, dataclasses, etc.) keep their existing source from `_orchestrator`. Run `just format` after if ruff splits the import block.

- [ ] **Step 6: Run the full gate**

Run:

```bash
just test && just lint && just typecheck && just smoke && just eval
```

Expected: green. The gate's import-linter run also exercises the `ingest-private` contract — confirm it still reads "KEPT".

If `just lint` flags F401 dead imports in `_orchestrator.py`, run `just format` to auto-fix and re-run lint to confirm.

If a test fails, the most likely cause is a name in `ingest()` that wasn't on Step 2's inventory and isn't being imported in `_ingest.py` — the test traceback names it; add the missing import and re-run.

- [x] **Step 7: Confirm the cycle is gone**

Run:

```bash
grep -n "noqa: E402" slopmortem/ingest/_orchestrator.py
```

Expected: no matches. The two runtime-circular-import escape hatches should be gone.

Also verify the import direction:

```bash
grep -n "from slopmortem\.ingest\." slopmortem/ingest/_orchestrator.py
```

Expected: no matches. `_orchestrator.py` should now import only from outside the `slopmortem.ingest` package.

---

## Out of scope

- Renaming `_orchestrator.py` to something more accurate (e.g. `_internal.py`, `_types.py`). One rename, one PR — pick it up next if the misnomer feels load-bearing.
- Touching `_warm_cache.py`, `_fan_out.py`, `_journal_writes.py`, `_slop_gate.py`. Their dependency on `_orchestrator` for types/helpers stays as-is.
- Trimming `_orchestrator.__all__`. The package façade in `__init__.py` is the public surface; `_orchestrator.__all__` is internal.
