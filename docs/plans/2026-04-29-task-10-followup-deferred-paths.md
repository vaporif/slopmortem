# Task 10 deferred paths — second follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task in sequence, per the Execution Strategy below. Steps use checkbox (`- [ ]`) syntax — these are **persistent durable state**, not visual decoration. The executor edits the plan file in place: `- [ ]` → `- [x]` the instant a step verifies, before moving on. On resume (new session, crash, takeover), the executor scans existing `- [x]` marks and skips them — these steps are NOT redone. TodoWrite mirrors this state in-session; the plan file is the source of truth across sessions.

**Goal:** Close the four ingest CLI paths and the one synthesize-side budget gate that Plan #1 (`2026-04-29-task-10-carryovers.md`) deferred: ship `TavilyEnricher` so `--tavily-enrich` works, wire `--list-review` and `--reconcile` to existing backend functions, implement the `--reclassify` orchestrator, and enforce the per-synthesis Tavily budget cap (≤2 calls/synthesis) named in spec line 1005.

**Architecture:** Two of the five tasks are pure wiring; the heavy lifting (`reconcile()` and the `pending_review` table) already exists in `slopmortem/corpus/reconcile.py` and `slopmortem/corpus/merge.py`. Three tasks add new code: a `TavilyEnricher` adapter (mirrors `WaybackEnricher`), a `reclassify_quarantined` orchestrator (walks `quarantine_journal`, re-scores via the current classifier, routes survivors through entity resolution), and a tool-call counter that wraps the Tavily `ToolSpec.fn` inside `synthesis_tools(config)` to reject calls past the cap. No new dependencies.

**Tech Stack:** Same as Plan #1. Inherits `safe_post` from Plan #1 Task A; this plan does not extend `slopmortem/http.py`.

**Source spec:** [`docs/specs/2026-04-27-slopmortem-design.md`](../specs/2026-04-27-slopmortem-design.md). Relevant sections per task block. Predecessor plans: [`docs/plans/2026-04-28-slopmortem-implementation.md`](2026-04-28-slopmortem-implementation.md), [`docs/plans/2026-04-29-task-10-carryovers.md`](2026-04-29-task-10-carryovers.md).

**Hard prerequisite:** Plan #1 must be complete before this plan starts. Specifically:
- Task A from Plan #1 must have shipped `safe_post` and the working Tavily synthesis tools; this plan's TavilyEnricher reuses `safe_post`, and this plan's Task E modifies the `synthesis_tools` factory.
- Task C from Plan #1 must have shipped the `_run_ingest` real wiring; this plan replaces the four "deferred" exit paths inside that wiring.

If Plan #1 is not complete, stop and finish it first. Do not interleave.

## Execution Strategy

**Selected: Sequential execution** (matches the parent plan's user-overridden Sequential strategy from `feedback_plan_execution.md`).

Tasks run one at a time in the listed order. Tasks B and C are pure wiring; they can finish in one short session each. Task A and Task D are the heavier add-on classes. Task E is a synthesize-side gate that lands last because it's independent from the ingest CLI work.

Order of execution:

1. **Task A**: `TavilyEnricher` (ingest-time enricher; un-stubs `--tavily-enrich`).
2. **Task B**: `--list-review` reader + CLI wiring.
3. **Task C**: `--reconcile` CLI wiring (`reconcile()` already implemented).
4. **Task D**: `--reclassify` orchestrator + CLI wiring.
5. **Task E**: Per-synthesis Tavily budget gate (≤2 calls/synthesis).

Implementation uses `superpowers:executing-plans`: read this plan, work the next unchecked task, run its TDD steps in order, mark each step done as it's verified, request review after the task closes, then move on. No fan-out, no agent teams. The user dispatches a single `python-development:python-pro` subagent per task with a self-contained brief.

## Agent Assignments

All code tasks use `python-development:python-pro`.

| # | Task | Agent type | Domain |
|---|------|------------|--------|
| A | `TavilyEnricher` (`slopmortem/corpus/sources/tavily.py`) + CLI un-stub for `--tavily-enrich` | python-development:python-pro | Python |
| B | `--list-review` reader + CLI wiring | python-development:python-pro | Python |
| C | `--reconcile` CLI wiring | python-development:python-pro | Python |
| D | `--reclassify` orchestrator + CLI wiring | python-development:python-pro | Python |
| E | Per-synthesis Tavily budget gate (≤2 calls/synthesis) | python-development:python-pro | Python |

---

## How to read this plan

Each task block has: **Files** (create / modify / test paths), **Spec refs** (line ranges in the design spec the implementer must read before starting), **TDD steps** (failing test → minimal impl → green), and **Verification** (commands and expected output).

Implementers should:

1. Read this task block.
2. Read the spec sections it references.
3. Run the TDD steps in order; do not batch.
4. Run the full sweep at the end of every task (`pytest`, `ruff check`, `ruff format --check`, `basedpyright`).
5. Flip `- [ ]` → `- [x]` after each step verifies.

uv is **not** on PATH. Use the project venv directly: `./.venv/bin/pytest`, `./.venv/bin/ruff`, `./.venv/bin/basedpyright`, `./.venv/bin/python`.

Subagents must not run `git add` / `git commit` (see `feedback_subagent_no_commits.md`). The parent owns commits. An external watcher may auto-commit edits anyway; best-effort.

---

## Task A: `TavilyEnricher` (ingest-time)

**Files:**
- Create: `slopmortem/corpus/sources/tavily.py`. Defines `TavilyEnricher` class implementing the `Enricher` Protocol from `slopmortem/corpus/sources/base.py`.
- Modify: `slopmortem/cli.py`. Replace the `--tavily-enrich` rejection in `_run_ingest` (added by Plan #1 Task C) with `enrichers.append(TavilyEnricher())`.
- Test: `tests/sources/test_tavily_enricher.py` (new). Tool-level enrichment tests with a mocked `safe_post`.

**Spec refs:** §Sources line 245 (`--tavily-enrich` is opt-in), §Synthesis tool registry line 1014 (Tavily-enrichment at ingest is a non-tool fetch step), §Auth line 207 (`TAVILY_API_KEY` env var).

### Why a separate class (not reuse the synthesis tools)

The synthesis-time Tavily tools from Plan #1 Task A are designed to be called *by the LLM* during synthesis (their results re-enter the conversation as `tool` messages). The ingest-time enricher runs *before* the LLM ever sees the doc; it just hits Tavily's extract endpoint to recover an article body that the source's primary fetch could not retrieve. Different contracts, different error semantics (an enricher returns the entry unchanged on failure; a tool raises). Reuse is shallow at best, and would couple two unrelated call sites.

### Step-by-step

- [x] **Step A.1: Read spec lines 245, 1014, and look at `slopmortem/corpus/sources/wayback.py` end-to-end** as the reference Enricher implementation.

- [x] **Step A.2: Write failing tests for `TavilyEnricher`.**

`tests/sources/test_tavily_enricher.py`:

```python
"""TavilyEnricher recovers article bodies via Tavily's /extract API."""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest

from slopmortem.corpus.sources.tavily import TavilyEnricher
from slopmortem.models import RawEntry


def _entry(*, raw_html: str | None = None, url: str | None = "https://example.com/x") -> RawEntry:
    return RawEntry(
        source="hn_algolia",
        source_id="abc123",
        url=url,
        raw_html=raw_html,
        markdown_text=None,
        fetched_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_skips_when_raw_html_already_populated(monkeypatch):
    """If raw_html is non-empty, the enricher returns the entry unchanged."""
    mock_post = AsyncMock()
    monkeypatch.setattr("slopmortem.corpus.sources.tavily.safe_post", mock_post)
    monkeypatch.setenv("TAVILY_API_KEY", "tv-test-key")

    entry = _entry(raw_html="<html>already there</html>")
    result = await TavilyEnricher().enrich(entry)
    assert result is entry
    mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_url_missing(monkeypatch):
    """If url is None, the enricher returns the entry unchanged."""
    mock_post = AsyncMock()
    monkeypatch.setattr("slopmortem.corpus.sources.tavily.safe_post", mock_post)
    monkeypatch.setenv("TAVILY_API_KEY", "tv-test-key")

    entry = _entry(url=None, raw_html=None)
    result = await TavilyEnricher().enrich(entry)
    assert result is entry
    mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_populates_raw_html_and_markdown_on_success(monkeypatch):
    """On a 200 with a results[0].raw_content, both raw_html and markdown_text fill."""
    fake_resp = httpx.Response(
        200,
        json={
            "results": [
                {
                    "url": "https://example.com/x",
                    "raw_content": "<p>recovered article body</p>",
                }
            ]
        },
    )
    mock_post = AsyncMock(return_value=fake_resp)
    monkeypatch.setattr("slopmortem.corpus.sources.tavily.safe_post", mock_post)
    monkeypatch.setenv("TAVILY_API_KEY", "tv-test-key")

    entry = _entry(raw_html=None)
    result = await TavilyEnricher().enrich(entry)
    assert result is not entry  # immutable update
    assert result.raw_html == "<p>recovered article body</p>"
    assert result.markdown_text  # extract_clean filled this


@pytest.mark.asyncio
async def test_returns_entry_unchanged_on_http_error(monkeypatch):
    """A non-200 from Tavily is logged and the entry passes through unchanged."""
    fake_resp = httpx.Response(429, json={"detail": "rate limited"})
    monkeypatch.setattr(
        "slopmortem.corpus.sources.tavily.safe_post", AsyncMock(return_value=fake_resp)
    )
    monkeypatch.setenv("TAVILY_API_KEY", "tv-test-key")

    entry = _entry(raw_html=None)
    result = await TavilyEnricher().enrich(entry)
    assert result is entry
    assert result.raw_html is None


@pytest.mark.asyncio
async def test_returns_entry_unchanged_when_api_key_missing(monkeypatch):
    """No TAVILY_API_KEY → enricher logs and returns the entry unchanged (does not raise)."""
    mock_post = AsyncMock()
    monkeypatch.setattr("slopmortem.corpus.sources.tavily.safe_post", mock_post)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    entry = _entry(raw_html=None)
    result = await TavilyEnricher().enrich(entry)
    assert result is entry
    mock_post.assert_not_called()
```

**Why "return unchanged on missing API key" rather than raise:** the Enricher contract is best-effort. Wayback handles its failures the same way (returns the entry unchanged on robots block, fetch failure, or empty payload). Raising at ingest start would be surprising; logging once and skipping every entry afterwards is the consistent choice.

- [x] **Step A.3: Run the tests; confirm they fail.**

```
./.venv/bin/pytest tests/sources/test_tavily_enricher.py -v
```

Expected: 5 tests fail with `ModuleNotFoundError: slopmortem.corpus.sources.tavily`.

- [x] **Step A.4: Implement `TavilyEnricher`.**

`slopmortem/corpus/sources/tavily.py`:

```python
"""TavilyEnricher — recovers article bodies via Tavily /extract for empty raw_html."""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import httpx

from slopmortem.corpus.extract import extract_clean
from slopmortem.http import safe_post

if TYPE_CHECKING:
    from slopmortem.models import RawEntry

logger = logging.getLogger(__name__)

_TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"


class TavilyEnricher:
    """[Enricher] Tavily /extract client that recovers article bodies on empty entries."""

    async def enrich(self, entry: RawEntry) -> RawEntry:
        """Populate ``raw_html``/``markdown_text`` from Tavily when the live URL is dead.

        Best-effort. Returns *entry* unchanged on missing API key, missing URL,
        already-populated raw_html, HTTP error, empty response, or any
        Tavily-side failure.
        """
        if entry.raw_html is not None and entry.raw_html.strip():
            return entry
        if not entry.url:
            return entry
        api_key = os.environ.get("TAVILY_API_KEY", "")
        if not api_key:
            logger.warning("tavily enricher: TAVILY_API_KEY not set; skipping")
            return entry

        try:
            resp = await safe_post(
                _TAVILY_EXTRACT_URL,
                json={"api_key": api_key, "urls": [entry.url]},
            )
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("tavily enricher: fetch failed for %s: %s", entry.url, exc)
            return entry

        if resp.status_code >= 400:
            logger.warning("tavily enricher: HTTP %s for %s", resp.status_code, entry.url)
            return entry

        try:
            payload = resp.json()
        except ValueError:
            return entry

        results = payload.get("results") if isinstance(payload, dict) else None
        if not results:
            return entry

        first = results[0] if isinstance(results, list) and results else None
        if not isinstance(first, dict):
            return entry

        raw_content = first.get("raw_content")
        if not isinstance(raw_content, str) or not raw_content:
            return entry

        markdown_text = extract_clean(raw_content) or None
        return entry.model_copy(update={"raw_html": raw_content, "markdown_text": markdown_text})
```

- [x] **Step A.5: Run the tests; confirm they pass.**

```
./.venv/bin/pytest tests/sources/test_tavily_enricher.py -v
```

Expected: 5 passed.

- [x] **Step A.6: Wire `--tavily-enrich` in the CLI.**

In `slopmortem/cli.py:_run_ingest`, find the block Plan #1 Task C added:

```python
if tavily_enrich:
    typer.echo(
        "--tavily-enrich is deferred to a follow-up plan; "
        "TavilyEnricher is not implemented in this iteration.",
        err=True,
    )
    raise typer.Exit(code=1)
```

Delete that block. Add the import at the top:

```python
from slopmortem.corpus.sources.tavily import TavilyEnricher
```

In the enrichers-construction block, append `TavilyEnricher()` when the flag is set:

```python
enrichers: list[Enricher] = []
if enrich_wayback:
    enrichers.append(WaybackEnricher())
if tavily_enrich:
    enrichers.append(TavilyEnricher())
```

- [x] **Step A.7: Update the test from Plan #1 Task C that asserted `--tavily-enrich` rejected.**

`tests/test_cli_ingest.py:test_ingest_tavily_enrich_rejected` — invert it to `test_ingest_tavily_enrich_appends_enricher`. Capture the enrichers passed to the orchestrator and assert `TavilyEnricher` appears:

```python
def test_ingest_tavily_enrich_appends_enricher(monkeypatch, tmp_path):
    captured = {}

    async def fake_ingest(*, enrichers, **kwargs):
        captured["enrichers"] = enrichers
        return MagicMock(dry_run=True, processed=0)

    monkeypatch.setattr("slopmortem.cli.ingest", fake_ingest)
    monkeypatch.setattr(
        "slopmortem.cli._build_ingest_deps",
        lambda config, post_mortems_root: (
            MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()
        ),
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["ingest", "--dry-run", "--tavily-enrich", "--post-mortems-root", str(tmp_path)],
    )
    assert result.exit_code == 0, result.output
    enricher_classnames = [type(e).__name__ for e in captured["enrichers"]]
    assert "TavilyEnricher" in enricher_classnames
```

- [x] **Step A.8: Run the full sweep.**

```
./.venv/bin/pytest tests/ -q
./.venv/bin/ruff check slopmortem tests
./.venv/bin/ruff format --check slopmortem tests
./.venv/bin/basedpyright slopmortem tests
```

Expected: all green; test count = (Plan #1 baseline) + 5 new + 1 test inverted (no count delta on the inversion) = +5.

---

## Task B: `--list-review` reader + CLI wiring

**Files:**
- Modify: `slopmortem/corpus/merge.py`. Add `MergeJournal.list_pending_review() -> list[PendingReviewRow]` reader.
- Modify: `slopmortem/cli.py`. Replace the `--list-review` rejection with a real path that queries the journal, prints the queue, and exits 0.
- Modify: `slopmortem/models.py`. Add `PendingReviewRow(BaseModel)` with the columns the spec line 264 lists. **Note:** this is the one place this plan adds a domain model. The "no new BaseModels in evals" invariant from Task 11 applies to `slopmortem/evals/`; `models.py` is the sanctioned home for domain models, so adding a new row-shape model here is in-bounds.
- Test: `tests/test_list_review.py` (new). Exercises both the journal reader and the CLI path.

**Spec refs:** §Borderline-pair review line 264 (the `--list-review` printout), §Slop filter / pending_review line 444+, §`pending_review` table schema (`slopmortem/corpus/merge.py` lines 76–82, already present).

### Step-by-step

- [x] **Step B.1: Read spec line 264 and the existing `pending_review` table schema in `slopmortem/corpus/merge.py:76`.**

- [x] **Step B.2: Define `PendingReviewRow` failing test.**

`tests/test_list_review.py`:

```python
"""--list-review reads the pending_review table and prints to stdout."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from slopmortem.cli import app
from slopmortem.corpus.merge import MergeJournal
from slopmortem.models import PendingReviewRow


def test_pending_review_row_round_trips():
    row = PendingReviewRow(
        pair_key="acme:beta",
        similarity_score=0.78,
        haiku_decision="merge",
        haiku_rationale="same product, parent rebrand",
        raw_section_heads="acme=…|beta=…",
    )
    assert row.pair_key == "acme:beta"


@pytest.mark.asyncio
async def test_list_pending_review_returns_rows(tmp_path: Path):
    """Insert two pending_review rows directly, assert the reader returns them."""
    db = tmp_path / "journal.sqlite"
    journal = MergeJournal(db)
    await journal.init()

    # Insert two rows by going through the existing _write_pending_review_sync path.
    # If that helper is not directly callable (private), use a raw connection
    # and INSERT — the table contract is the source of truth.
    import sqlite3  # noqa: PLC0415
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO pending_review VALUES (?, ?, ?, ?, ?)",
        ("acme:beta", 0.78, "merge", "same product", "acme=…|beta=…"),
    )
    conn.execute(
        "INSERT INTO pending_review VALUES (?, ?, ?, ?, ?)",
        ("foo:bar", 0.83, "no_merge", "different segments", "foo=…|bar=…"),
    )
    conn.commit()
    conn.close()

    rows = await journal.list_pending_review()
    assert len(rows) == 2
    keys = {r.pair_key for r in rows}
    assert keys == {"acme:beta", "foo:bar"}


def test_cli_list_review_prints_queue(monkeypatch, tmp_path: Path):
    """--list-review queries the journal and prints rows."""
    fake_rows = [
        PendingReviewRow(
            pair_key="acme:beta",
            similarity_score=0.78,
            haiku_decision="merge",
            haiku_rationale="same product",
            raw_section_heads="acme=…|beta=…",
        )
    ]

    fake_journal = MagicMock()
    fake_journal.list_pending_review = MagicMock(return_value=fake_rows)
    # list_pending_review is async; wrap in an async return.
    async def _afake():
        return fake_rows
    fake_journal.list_pending_review = _afake

    monkeypatch.setattr(
        "slopmortem.cli._build_ingest_deps",
        lambda config, post_mortems_root: (
            MagicMock(), MagicMock(), MagicMock(),
            MagicMock(), fake_journal, MagicMock(),
        ),
    )

    runner = CliRunner()
    result = runner.invoke(
        app, ["ingest", "--list-review", "--post-mortems-root", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert "acme:beta" in result.output
    assert "0.78" in result.output
```

- [x] **Step B.3: Run the tests; confirm they fail.**

```
./.venv/bin/pytest tests/test_list_review.py -v
```

Expected: imports fail (`PendingReviewRow` does not exist), or runtime failures (`list_pending_review` does not exist).

- [x] **Step B.4: Add `PendingReviewRow` to `slopmortem/models.py`.**

```python
class PendingReviewRow(BaseModel):
    """A row in the entity-resolution pending_review queue (spec line 264)."""

    pair_key: str
    similarity_score: float | None
    haiku_decision: str | None
    haiku_rationale: str | None
    raw_section_heads: str | None
```

- [x] **Step B.5: Add `MergeJournal.list_pending_review` to `slopmortem/corpus/merge.py`.**

```python
async def list_pending_review(self) -> list[PendingReviewRow]:
    """Read all rows from the ``pending_review`` table.

    Returns rows in INSERT order (no explicit ORDER BY — ``--list-review``
    is exploratory; the caller can sort if it cares about ordering).
    """
    return await asyncio.to_thread(self._list_pending_review_sync)


def _list_pending_review_sync(self) -> list[PendingReviewRow]:
    with closing(_connect(self._db_path)) as conn:
        cur = conn.execute("SELECT * FROM pending_review")
        return [
            PendingReviewRow(
                pair_key=row["pair_key"],
                similarity_score=row["similarity_score"],
                haiku_decision=row["haiku_decision"],
                haiku_rationale=row["haiku_rationale"],
                raw_section_heads=row["raw_section_heads"],
            )
            for row in cur.fetchall()
        ]
```

The exact private helper signature depends on the existing `MergeJournal` patterns (run `grep "to_thread\|_sync" slopmortem/corpus/merge.py` and follow the existing form).

- [x] **Step B.6: Wire the CLI path.**

In `slopmortem/cli.py:_run_ingest`, find the deferred-flag block Plan #1 added that exits 1 on `list_review` and replace with a real path. Order matters — `list_review` must be checked BEFORE the orchestrator dispatches:

```python
if reconcile or reclassify or list_review:
    # ... existing rejection still applies to reconcile + reclassify ...

# becomes:
config = load_config()
_maybe_init_tracing(config)
llm, embedder, corpus, budget, journal, classifier = _build_ingest_deps(
    config, post_mortems_root
)

if list_review:
    rows = await journal.list_pending_review()
    if not rows:
        typer.echo("(no pending_review rows)")
    for r in rows:
        typer.echo(
            f"{r.pair_key}\tscore={r.similarity_score}\t"
            f"decision={r.haiku_decision}\trationale={r.haiku_rationale}"
        )
    return  # exit 0

if reconcile or reclassify:
    # ... unchanged rejection until Tasks C / D land ...
```

- [x] **Step B.7: Run the tests; confirm they pass.**

```
./.venv/bin/pytest tests/test_list_review.py -v
./.venv/bin/pytest tests/test_cli_ingest.py -v  # the deferred-flag parametrize must drop --list-review
```

Update `test_ingest_deferred_flags_rejected` from Plan #1 to no longer parametrize over `--list-review` — now only `--reconcile` and `--reclassify` are deferred.

- [x] **Step B.8: Run the full sweep.**

```
./.venv/bin/pytest tests/ -q
./.venv/bin/ruff check slopmortem tests
./.venv/bin/ruff format --check slopmortem tests
./.venv/bin/basedpyright slopmortem tests
```

Expected: all green; test count = +3 (one model round-trip, one journal reader, one CLI path).

---

## Task C: `--reconcile` CLI wiring

**Files:**
- Modify: `slopmortem/cli.py`. Replace the `--reconcile` rejection with a real path that calls the existing `slopmortem.corpus.reconcile.reconcile()` and prints the report.
- Test: `tests/test_cli_reconcile.py` (new). Exercises the CLI path with a monkeypatched `reconcile()`.

**Spec refs:** §Atomicity / reconcile line 237, §Six drift classes line 604 (a–f), §Reconcile span events line 925.

### Why this is the smallest task

`slopmortem/corpus/reconcile.py:291` already implements `async def reconcile(journal, corpus, post_mortems_root, *, repair=False) -> ReconcileReport`, with all six drift classes scanned and `_apply_repairs` for repair mode. The CLI just calls it with `repair=True` (the spec says `--reconcile` repairs, not just scans) and pretty-prints the report. No behavioral change to the orchestrator.

### Step-by-step

- [x] **Step C.1: Read `slopmortem/corpus/reconcile.py:reconcile`'s signature and `ReconcileReport`'s shape.**

```
./.venv/bin/python -c "
from slopmortem.corpus.reconcile import reconcile, ReconcileReport
import inspect
print(inspect.signature(reconcile))
print(ReconcileReport.model_fields)
"
```

If the signature differs from what this plan assumes, adjust the wiring below. Do not modify `reconcile.py` itself.

- [x] **Step C.2: Write failing CLI test.**

`tests/test_cli_reconcile.py`:

```python
"""--reconcile dispatches to slopmortem.corpus.reconcile.reconcile and prints the report."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

from slopmortem.cli import app
from slopmortem.corpus.reconcile import ReconcileReport


def test_cli_reconcile_dispatches_with_repair_true(monkeypatch, tmp_path: Path):
    fake_report = ReconcileReport(rows=[], applied=[])
    fake_reconcile = AsyncMock(return_value=fake_report)
    monkeypatch.setattr("slopmortem.cli.reconcile", fake_reconcile)
    monkeypatch.setattr(
        "slopmortem.cli._build_ingest_deps",
        lambda config, post_mortems_root: (
            MagicMock(), MagicMock(),
            MagicMock(name="corpus"),
            MagicMock(),
            MagicMock(name="journal"),
            MagicMock(),
        ),
    )
    runner = CliRunner()
    result = runner.invoke(
        app, ["ingest", "--reconcile", "--post-mortems-root", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    fake_reconcile.assert_awaited_once()
    kwargs = fake_reconcile.await_args.kwargs
    assert kwargs.get("repair") is True
    # Report contents printed to stdout.
    assert "reconcile" in result.output.lower()


def test_cli_reconcile_prints_drift_findings(monkeypatch, tmp_path: Path):
    """When the report has rows, each one shows up in the printed output."""
    from slopmortem.corpus.reconcile import ReconcileRow  # noqa: PLC0415

    fake_report = ReconcileReport(
        rows=[
            ReconcileRow(drift_class="a", path="canonical/abc.md", action="re-embed"),
            ReconcileRow(drift_class="e", path="canonical/abc.md.tmp", action="delete"),
        ],
        applied=["a:canonical/abc.md", "e:canonical/abc.md.tmp"],
    )
    monkeypatch.setattr("slopmortem.cli.reconcile", AsyncMock(return_value=fake_report))
    monkeypatch.setattr(
        "slopmortem.cli._build_ingest_deps",
        lambda config, post_mortems_root: (
            MagicMock(), MagicMock(), MagicMock(),
            MagicMock(), MagicMock(), MagicMock(),
        ),
    )
    runner = CliRunner()
    result = runner.invoke(
        app, ["ingest", "--reconcile", "--post-mortems-root", str(tmp_path)]
    )
    assert result.exit_code == 0
    assert "drift_class=a" in result.output or "class=a" in result.output
    assert "canonical/abc.md.tmp" in result.output
```

The exact `ReconcileRow` field names should be lifted from the actual `slopmortem/corpus/reconcile.py` source — this plan's example uses `drift_class`, `path`, `action`, but the real shape may vary. Step C.1's grep confirms the schema before writing the test.

- [x] **Step C.3: Run the tests; confirm they fail.**

```
./.venv/bin/pytest tests/test_cli_reconcile.py -v
```

Expected: 2 tests fail because the CLI still rejects `--reconcile`.

- [x] **Step C.4: Wire the CLI path.**

In `slopmortem/cli.py`, add the import:

```python
from slopmortem.corpus.reconcile import reconcile
```

Replace the `--reconcile` branch in `_run_ingest`:

```python
if reconcile_flag:  # rename param to avoid shadowing the imported function
    report = await reconcile(
        journal=journal,
        corpus=corpus,
        post_mortems_root=post_mortems_root,
        repair=True,
    )
    typer.echo(f"reconcile: {len(report.rows)} drift findings, {len(report.applied)} repaired")
    for r in report.rows:
        typer.echo(f"  drift_class={r.drift_class}\t{r.path}\t{r.action}")
    return
```

**Note:** Python parameter named `reconcile` in `_run_ingest` shadows the imported function. Either rename the typer Option to a different Python name (e.g. `reconcile_flag: Annotated[bool, typer.Option("--reconcile", ...)]`), or import the function under an alias (`from slopmortem.corpus.reconcile import reconcile as _reconcile`). Pick one and stay consistent in `_run_ingest`.

- [x] **Step C.5: Update `_run_ingest`'s deferred-flag block.**

Drop `reconcile` from the rejection condition; only `reclassify` remains deferred until Task D lands. Update the parametrized `test_ingest_deferred_flags_rejected` similarly.

- [x] **Step C.6: Run the tests; confirm they pass.**

```
./.venv/bin/pytest tests/test_cli_reconcile.py -v
./.venv/bin/pytest tests/test_cli_ingest.py -v
```

- [x] **Step C.7: Full sweep.**

```
./.venv/bin/pytest tests/ -q
./.venv/bin/ruff check slopmortem tests
./.venv/bin/ruff format --check slopmortem tests
./.venv/bin/basedpyright slopmortem tests
```

Expected: all green; test count = +2.

---

## Task D: `--reclassify` orchestrator + CLI wiring

**Files:**
- Create: `slopmortem/corpus/reclassify.py`: `async def reclassify_quarantined(...) -> ReclassifyReport`.
- Modify: `slopmortem/models.py`. Add `ReclassifyReport(BaseModel)` (counts of declassified / still-slop / errors).
- Modify: `slopmortem/cli.py`. Replace `--reclassify` rejection with a real path.
- Test: `tests/test_reclassify.py` (new). Orchestrator unit tests.
- Test: `tests/test_cli_reclassify.py` (new). CLI smoke test.

**Spec refs:** §Quarantine and reclassify line 252 ("`slopmortem ingest --reclassify` re-runs the classifier when the threshold or model is updated; declassified docs flow into entity resolution"), §Quarantine routing lines 408+ (the `quarantine_journal` table shape).

### Algorithm

1. Open `MergeJournal`; read every row from `quarantine_journal`.
2. For each row, read the quarantined doc body from `data/post_mortems/quarantine/<content_sha256>.md`.
3. Re-score via the current `SlopClassifier`.
4. If the new score is below the configured `slop_threshold`, the doc is declassified:
   - Remove the row from `quarantine_journal`.
   - Move the markdown file from `quarantine/` to `raw/<source>/<text_id>.md` (text_id derived as it would have been at original ingest; verify the path constructor in `slopmortem/corpus/paths.py:safe_path`).
   - Insert a row into the main merge journal with `merge_state="pending"` so the next normal `ingest` run picks it up and routes it through entity resolution + merge.
5. If the new score is still above threshold, leave the row in place.
6. Return a `ReclassifyReport` with counts: `total`, `declassified`, `still_slop`, `errors`.

### Step-by-step

- [x] **Step D.1: Read spec lines 252 and 408–445** (the quarantine routing diagram and reclassify semantics).

- [x] **Step D.2: Verify `quarantine_journal` schema** (`slopmortem/corpus/merge.py` lines 56–62).

The columns are `(content_sha256, source, source_id, reason, slop_score, quarantined_at)` per the schema in merge.py. Confirm by reading those lines directly before writing tests.

- [x] **Step D.3: Write failing orchestrator tests.**

`tests/test_reclassify.py`:

```python
"""reclassify_quarantined: re-score quarantined docs; route survivors to merge journal."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from slopmortem.corpus.merge import MergeJournal
from slopmortem.corpus.reclassify import reclassify_quarantined


@pytest.mark.asyncio
async def test_declassifies_doc_below_threshold(tmp_path: Path):
    """A quarantined doc that now scores below threshold is moved to raw/ and merge journal row added."""
    db = tmp_path / "journal.sqlite"
    quarantine_root = tmp_path / "post_mortems" / "quarantine"
    raw_root = tmp_path / "post_mortems" / "raw"
    quarantine_root.mkdir(parents=True)
    raw_root.mkdir(parents=True)

    journal = MergeJournal(db)
    await journal.init()

    # Insert one quarantined doc.
    sha = "a" * 64
    quarantine_md = quarantine_root / f"{sha}.md"
    quarantine_md.write_text("legitimate post-mortem body", encoding="utf-8")

    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO quarantine_journal VALUES (?, ?, ?, ?, ?, ?)",
        (sha, "hn_algolia", "story-123", "slop_score>0.7", 0.85, "2026-04-29T00:00:00Z"),
    )
    conn.commit()
    conn.close()

    # Classifier now scores it 0.4 (under the 0.7 threshold).
    fake_classifier = AsyncMock()
    fake_classifier.score = AsyncMock(return_value=0.4)

    report = await reclassify_quarantined(
        journal=journal,
        slop_classifier=fake_classifier,
        post_mortems_root=tmp_path / "post_mortems",
        slop_threshold=0.7,
    )

    assert report.total == 1
    assert report.declassified == 1
    assert report.still_slop == 0
    # Quarantine row is gone.
    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT * FROM quarantine_journal").fetchall()
    conn.close()
    assert len(rows) == 0
    # File moved to raw/<source>/<text_id>.md (text_id derivation per safe_path).
    assert not quarantine_md.exists()


@pytest.mark.asyncio
async def test_keeps_doc_above_threshold(tmp_path: Path):
    """A quarantined doc that still scores above threshold stays in quarantine."""
    db = tmp_path / "journal.sqlite"
    journal = MergeJournal(db)
    await journal.init()
    quarantine_root = tmp_path / "post_mortems" / "quarantine"
    quarantine_root.mkdir(parents=True)

    sha = "b" * 64
    (quarantine_root / f"{sha}.md").write_text("slop content", encoding="utf-8")
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO quarantine_journal VALUES (?, ?, ?, ?, ?, ?)",
        (sha, "hn_algolia", "story-456", "slop_score>0.7", 0.95, "2026-04-29T00:00:00Z"),
    )
    conn.commit()
    conn.close()

    fake_classifier = AsyncMock()
    fake_classifier.score = AsyncMock(return_value=0.85)  # still above 0.7

    report = await reclassify_quarantined(
        journal=journal,
        slop_classifier=fake_classifier,
        post_mortems_root=tmp_path / "post_mortems",
        slop_threshold=0.7,
    )

    assert report.total == 1
    assert report.declassified == 0
    assert report.still_slop == 1


@pytest.mark.asyncio
async def test_handles_missing_quarantine_file(tmp_path: Path):
    """A quarantine_journal row whose markdown file is missing increments errors and continues."""
    db = tmp_path / "journal.sqlite"
    journal = MergeJournal(db)
    await journal.init()

    sha = "c" * 64
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO quarantine_journal VALUES (?, ?, ?, ?, ?, ?)",
        (sha, "hn_algolia", "story-789", "slop", 0.9, "2026-04-29T00:00:00Z"),
    )
    conn.commit()
    conn.close()

    fake_classifier = AsyncMock()
    fake_classifier.score = AsyncMock(return_value=0.0)

    report = await reclassify_quarantined(
        journal=journal,
        slop_classifier=fake_classifier,
        post_mortems_root=tmp_path / "post_mortems",
        slop_threshold=0.7,
    )

    assert report.total == 1
    assert report.errors == 1
    assert report.declassified == 0
```

`tests/test_cli_reclassify.py`:

```python
"""--reclassify dispatches to slopmortem.corpus.reclassify.reclassify_quarantined."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from typer.testing import CliRunner

from slopmortem.cli import app
from slopmortem.models import ReclassifyReport


def test_cli_reclassify_dispatches(monkeypatch, tmp_path: Path):
    fake_report = ReclassifyReport(total=3, declassified=1, still_slop=2, errors=0)
    fake_reclassify = AsyncMock(return_value=fake_report)
    monkeypatch.setattr(
        "slopmortem.cli.reclassify_quarantined", fake_reclassify
    )
    monkeypatch.setattr(
        "slopmortem.cli._build_ingest_deps",
        lambda config, post_mortems_root: (
            MagicMock(), MagicMock(), MagicMock(),
            MagicMock(), MagicMock(name="journal"), MagicMock(name="classifier"),
        ),
    )
    runner = CliRunner()
    result = runner.invoke(
        app, ["ingest", "--reclassify", "--post-mortems-root", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    fake_reclassify.assert_awaited_once()
    assert "declassified=1" in result.output
    assert "still_slop=2" in result.output
```

- [x] **Step D.4: Run the tests; confirm they fail.**

```
./.venv/bin/pytest tests/test_reclassify.py tests/test_cli_reclassify.py -v
```

Expected: all fail with `ImportError`.

- [x] **Step D.5: Add `ReclassifyReport` to `slopmortem/models.py`.**

```python
class ReclassifyReport(BaseModel):
    """Result of a ``slopmortem ingest --reclassify`` pass."""

    total: int
    declassified: int
    still_slop: int
    errors: int
```

- [x] **Step D.6: Implement `reclassify_quarantined` in `slopmortem/corpus/reclassify.py`.**

```python
"""Re-score quarantined docs; declassify survivors and route through entity resolution."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from slopmortem.corpus.paths import safe_path
from slopmortem.models import ReclassifyReport

if TYPE_CHECKING:
    from slopmortem.corpus.merge import MergeJournal
    from slopmortem.ingest import SlopClassifier

logger = logging.getLogger(__name__)


async def reclassify_quarantined(
    *,
    journal: MergeJournal,
    slop_classifier: SlopClassifier,
    post_mortems_root: Path,
    slop_threshold: float,
) -> ReclassifyReport:
    """Re-run the slop classifier against every row in ``quarantine_journal``.

    Survivors (new score < ``slop_threshold``) are removed from the
    quarantine journal, their markdown files moved into the raw tree,
    and a ``merge_state="pending"`` row is inserted in the main merge
    journal so the next ``ingest`` run picks them up through the normal
    entity-resolution path. Docs that still score above threshold stay
    in quarantine. Missing markdown files increment ``errors``.
    """
    rows = await journal.list_quarantine_journal()  # add this reader if missing
    total = 0
    declassified = 0
    still_slop = 0
    errors = 0
    for row in rows:
        total += 1
        sha = row.content_sha256
        try:
            quarantine_path = safe_path(
                post_mortems_root, kind="quarantine", text_id=sha
            )
        except ValueError:
            errors += 1
            continue
        if not quarantine_path.exists():
            errors += 1
            logger.warning("reclassify: missing quarantine file for %s", sha)
            continue
        body = quarantine_path.read_text(encoding="utf-8")
        new_score = await slop_classifier.score(body)
        if new_score < slop_threshold:
            # Move file to raw tree.
            raw_path = safe_path(
                post_mortems_root, kind="raw", text_id=sha, source=row.source
            )
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            quarantine_path.rename(raw_path)
            # Drop the quarantine_journal row.
            await journal.drop_quarantine_row(sha=sha, source=row.source, source_id=row.source_id)
            # Insert a pending merge row so next ingest picks it up.
            await journal.write_pending_merge_row(
                canonical_id=None,  # entity resolution assigns at next ingest
                source=row.source,
                source_id=row.source_id,
                content_sha256=sha,
            )
            declassified += 1
        else:
            still_slop += 1
    return ReclassifyReport(
        total=total, declassified=declassified, still_slop=still_slop, errors=errors
    )
```

The exact `MergeJournal` reader / writer method names are placeholders. Step D.7 fills in the missing ones.

- [x] **Step D.7: Add `MergeJournal.list_quarantine_journal`, `drop_quarantine_row`, and `write_pending_merge_row` if not already present.**

These three methods may or may not exist on `MergeJournal` today. Run:

```
grep -n "def.*quarantine\|def.*pending_merge\|def.*write_pending" slopmortem/corpus/merge.py
```

For each that does not exist, add it next to existing analogous methods (`_write_pending_review_sync` is the style template). Each writer must dispatch via `asyncio.to_thread`.

If `list_quarantine_journal` already exists under a different name (e.g. `_iter_quarantine`), use that; do not invent a duplicate. Document the chosen name in `reclassify.py`.

- [x] **Step D.8: Wire the CLI path.**

In `slopmortem/cli.py`:

```python
from slopmortem.corpus.reclassify import reclassify_quarantined
```

Replace the `--reclassify` branch:

```python
if reclassify:
    report = await reclassify_quarantined(
        journal=journal,
        slop_classifier=classifier,
        post_mortems_root=post_mortems_root,
        slop_threshold=config.slop_threshold,
    )
    typer.echo(
        f"reclassify: total={report.total} declassified={report.declassified} "
        f"still_slop={report.still_slop} errors={report.errors}"
    )
    return
```

- [x] **Step D.9: Update `test_ingest_deferred_flags_rejected`** — drop `--reclassify` from the parametrize. The test now has zero parameters; delete it and any companion deferred-flag tests entirely.

- [x] **Step D.10: Run the tests.**

```
./.venv/bin/pytest tests/test_reclassify.py tests/test_cli_reclassify.py tests/test_cli_ingest.py -v
```

- [x] **Step D.11: Full sweep.**

```
./.venv/bin/pytest tests/ -q
./.venv/bin/ruff check slopmortem tests
./.venv/bin/ruff format --check slopmortem tests
./.venv/bin/basedpyright slopmortem tests
```

Expected: all green; test count = +4 (3 orchestrator + 1 CLI).

---

## Task E: Per-synthesis Tavily budget gate (≤2 calls/synthesis)

**Files:**
- Modify: `slopmortem/llm/tools.py`. Wrap the Tavily `ToolSpec.fn`s in `synthesis_tools(config)` with a per-call counter that rejects after the cap.
- Test: `tests/test_synthesis_tools.py` (existing). Add cases for the new gate.

**Spec refs:** §Tavily call budget line 1005 ("when --tavily-synthesis is ON, Tavily tool calls are budgeted at ≤2 per synthesis to bound attacker-controlled query bandwidth").

### Why a per-synthesis counter (not a global one)

The `OpenRouterClient.complete()` tool-loop is per-call: one `complete()` invocation = one synthesis = one tool budget. Since `synthesize_all` calls `synthesize` once per top-N candidate (each one is a separate `complete()`), each synthesis gets its own budget. A counter scoped to a single `synthesis_tools(config)` invocation captures exactly that scope: each call to the factory returns a fresh closure. Implementation:

```python
def synthesis_tools(config: Config) -> list[ToolSpec]:
    tools = [get_post_mortem, search_corpus]
    if config.enable_tavily_synthesis:
        # One counter, shared between tavily_search and tavily_extract.
        # Each call to synthesis_tools(config) creates a fresh counter, so each
        # synthesize() call in synthesize_all gets its own quota.
        counter = {"used": 0}
        cap = config.tavily_calls_per_synthesis  # add to Config; default 2

        def _bounded(inner_fn):
            async def _wrapped(**kwargs):
                if counter["used"] >= cap:
                    return f"tavily call budget exceeded ({cap} per synthesis); refusing"
                counter["used"] += 1
                return await inner_fn(**kwargs)
            return _wrapped

        tools.extend([
            ToolSpec(
                name=tavily_search.name,
                description=tavily_search.description,
                args_model=tavily_search.args_model,
                fn=_bounded(tavily_search.fn),
            ),
            ToolSpec(
                name=tavily_extract.name,
                description=tavily_extract.description,
                args_model=tavily_extract.args_model,
                fn=_bounded(tavily_extract.fn),
            ),
        ])
    return tools
```

When the model exceeds the cap, the tool returns a string ("budget exceeded") instead of raising. That string flows back into the conversation as a `tool_result`, so the model sees the rejection and can adapt (continue with corpus tools or return a final answer). Raising would short-circuit the synthesis with no recovery.

The counter is shared between `tavily_search` and `tavily_extract` because the spec line 1005 talks about "Tavily tool calls" as one bucket; both endpoints consume the same attacker-controlled-query bandwidth.

### Step-by-step

- [x] **Step E.1: Read spec line 1005.** Confirm "≤2 per synthesis" is the right cap and that both Tavily tools share the budget.

- [x] **Step E.2: Verify the current `synthesis_tools` factory at `slopmortem/llm/tools.py:80`.** Read it end-to-end before modifying.

- [x] **Step E.3: Add `tavily_calls_per_synthesis: int = 2` to `slopmortem/config.py`.**

```python
tavily_calls_per_synthesis: int = 2  # spec line 1005
```

- [x] **Step E.4: Write failing tests.**

Append to `tests/test_synthesis_tools.py` (or create the file if it does not exist; check first):

```python
"""Per-synthesis Tavily budget gate (spec line 1005)."""
import pytest
from slopmortem.config import Config
from slopmortem.llm.tools import synthesis_tools


@pytest.mark.asyncio
async def test_tavily_calls_under_cap_pass_through(monkeypatch):
    """First two Tavily calls in one synthesis flow through to the real tool fn."""
    cfg = Config(enable_tavily_synthesis=True, tavily_calls_per_synthesis=2)
    tools = synthesis_tools(cfg)
    tavily = next(t for t in tools if t.name == "tavily_search")

    calls = []

    async def fake_real(*, q, limit=5):
        calls.append((q, limit))
        return f"hit:{q}"

    monkeypatch.setattr("slopmortem.corpus.tools_impl._tavily_search", fake_real)
    # Re-fetch tools so the new patched fn is the inner of the bounded wrapper.
    tools = synthesis_tools(cfg)
    tavily = next(t for t in tools if t.name == "tavily_search")

    out1 = await tavily.fn(q="acme", limit=5)
    out2 = await tavily.fn(q="beta", limit=5)
    assert "hit:acme" in out1
    assert "hit:beta" in out2
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_third_tavily_call_returns_budget_message(monkeypatch):
    """The third Tavily call in one synthesis returns a budget-exceeded string, not an exception."""
    cfg = Config(enable_tavily_synthesis=True, tavily_calls_per_synthesis=2)
    real_calls = []

    async def fake_real(*, q, limit=5):
        real_calls.append(q)
        return "ok"

    monkeypatch.setattr("slopmortem.corpus.tools_impl._tavily_search", fake_real)
    tools = synthesis_tools(cfg)
    tavily = next(t for t in tools if t.name == "tavily_search")

    await tavily.fn(q="a", limit=5)
    await tavily.fn(q="b", limit=5)
    out3 = await tavily.fn(q="c", limit=5)
    assert "budget exceeded" in out3
    assert real_calls == ["a", "b"]  # third call did NOT reach the real fn


@pytest.mark.asyncio
async def test_tavily_search_and_extract_share_budget(monkeypatch):
    """The cap covers tavily_search + tavily_extract combined, not each independently."""
    cfg = Config(enable_tavily_synthesis=True, tavily_calls_per_synthesis=2)

    async def fake_search(*, q, limit=5):
        return "search-hit"

    async def fake_extract(*, url):
        return "extract-hit"

    monkeypatch.setattr("slopmortem.corpus.tools_impl._tavily_search", fake_search)
    monkeypatch.setattr("slopmortem.corpus.tools_impl._tavily_extract", fake_extract)
    tools = synthesis_tools(cfg)
    search = next(t for t in tools if t.name == "tavily_search")
    extract = next(t for t in tools if t.name == "tavily_extract")

    await search.fn(q="a", limit=1)
    await extract.fn(url="https://example.com/x")
    out3 = await search.fn(q="b", limit=1)  # third call across the two tools
    assert "budget exceeded" in out3


@pytest.mark.asyncio
async def test_each_synthesis_gets_a_fresh_budget(monkeypatch):
    """Two separate calls to synthesis_tools(config) → two independent counters."""
    cfg = Config(enable_tavily_synthesis=True, tavily_calls_per_synthesis=1)

    async def fake_search(*, q, limit=5):
        return "ok"

    monkeypatch.setattr("slopmortem.corpus.tools_impl._tavily_search", fake_search)

    # Synthesis #1: exhausts the budget after one call.
    tools_a = synthesis_tools(cfg)
    search_a = next(t for t in tools_a if t.name == "tavily_search")
    out_a1 = await search_a.fn(q="a", limit=1)
    out_a2 = await search_a.fn(q="b", limit=1)
    assert "ok" in out_a1
    assert "budget exceeded" in out_a2

    # Synthesis #2: fresh tools, fresh budget.
    tools_b = synthesis_tools(cfg)
    search_b = next(t for t in tools_b if t.name == "tavily_search")
    out_b1 = await search_b.fn(q="x", limit=1)
    assert "ok" in out_b1


def test_tavily_disabled_means_no_tavily_tools_in_factory():
    """When enable_tavily_synthesis=False, the factory does not return Tavily tools."""
    cfg = Config(enable_tavily_synthesis=False)
    tools = synthesis_tools(cfg)
    names = {t.name for t in tools}
    assert "tavily_search" not in names
    assert "tavily_extract" not in names
```

- [x] **Step E.5: Run the tests; confirm they fail.**

```
./.venv/bin/pytest tests/test_synthesis_tools.py -v -k "tavily"
```

Expected: 4 of the 5 tests fail (the disabled-case test may pass already if the factory short-circuits). The cap-enforcement tests fail because no gate is in place.

- [x] **Step E.6: Implement the wrapper in `synthesis_tools`.**

Apply the closure-based pattern shown in the "Why a per-synthesis counter" section above. Keep `get_post_mortem` and `search_corpus` un-wrapped; only the two Tavily tools get the counter. The counter dict (`{"used": 0}`) is captured by the inner closure of each Tavily wrapper, and both wrappers share the same dict instance.

- [x] **Step E.7: Run the tests; confirm they pass.**

```
./.venv/bin/pytest tests/test_synthesis_tools.py -v
```

Expected: all 5 cases pass.

- [ ] **Step E.8: Run the existing pipeline e2e tests** — make sure the wrapper does not break anything when Tavily is disabled (default).

```
./.venv/bin/pytest tests/test_pipeline_e2e.py -v
```

Expected: same green count as before, 8 passed.

- [ ] **Step E.9: Full sweep.**

```
./.venv/bin/pytest tests/ -q
./.venv/bin/ruff check slopmortem tests
./.venv/bin/ruff format --check slopmortem tests
./.venv/bin/basedpyright slopmortem tests
```

Expected: all green; test count = +5 (or +4 if the disabled-case test already existed).

### Out of scope for Task E

- Per-tool cost accounting (`Budget` integration with Tavily). The Tavily tools cost real money (~$0.005/call); routing those into `Budget.spent_usd` is a separate concern. Spec doesn't require it for v1; defer.
- Smarter budgets keyed on argument content (e.g. "≤2 distinct query strings"). The spec says "≤2 calls"; the simple counter matches.

---

## Final verification (after all five tasks land)

- [ ] **Run the full sweep.**

```
./.venv/bin/pytest tests/ -v
./.venv/bin/ruff check slopmortem tests
./.venv/bin/ruff format --check slopmortem tests
./.venv/bin/basedpyright slopmortem tests
./.venv/bin/python -m slopmortem.evals.runner --dataset tests/evals/datasets/seed.jsonl --baseline tests/evals/baseline.json
```

Expected: all green; eval runner exits 0.

- [ ] **Confirm the spec invariants still hold.**

```
grep -RIn "deferred to a follow-up" slopmortem/cli.py
```

Expected: empty (no CLI flag still rejects).

```
grep -RIn "NotImplementedError" slopmortem/corpus/sources/tavily.py
```

Expected: empty.

```
grep -n "tavily_calls_per_synthesis" slopmortem/config.py slopmortem/llm/tools.py
```

Expected: present in both.

- [ ] **Smoke the CLI surface (no real network).**

```
./.venv/bin/python -m slopmortem.cli ingest --help
```

Expected: every flag in the help text now drives a real path; no flag exits with "deferred to a follow-up".

---

## What this plan deliberately does NOT cover

- Live integration testing against real Qdrant + OpenRouter + OpenAI + Tavily. Belongs in the parent plan's "Final integration review" section and requires `make smoke-live` plus user-driven setup.
- Interactive `--review` queue (accept / reject / split). Spec line 1099 explicitly defers this to v2; v1 only ships the printout.
- Tavily cost accounting through `Budget`. Out of scope; v1 spec doesn't require it.
- Per-source rate limiting beyond what `WaybackEnricher` already does via `respect_robots` + `throttle_for`. The new `TavilyEnricher` does NOT add throttling; Tavily's API tier handles that server-side.
- Task 4b from the parent plan (scaling curated YAML to ≥200 URLs). User-owned manual work; not agent-implementable.
