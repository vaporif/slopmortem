# Task 10 carry-overs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task in sequence, per the Execution Strategy below. Steps use checkbox (`- [ ]`) syntax — these are **persistent durable state**, not visual decoration. The executor edits the plan file in place: `- [ ]` → `- [x]` the instant a step verifies, before moving on. On resume (new session, crash, takeover), the executor scans existing `- [x]` marks and skips them — these steps are NOT redone. TodoWrite mirrors this state in-session; the plan file is the source of truth across sessions.

**Goal:** Close the three carry-overs left open at the end of the slopmortem v1 implementation: wire `slopmortem ingest` from a flag-echoing stub to a real orchestration path, decorate the three retrieval/extraction stages with `@observe` (without exfiltrating corpus body to remote tracing), and ship working Tavily synthesis tools so the `--tavily-synthesis` opt-in path no longer raises `NotImplementedError`.

**Architecture:** Three independent slices on top of the existing pipeline. The Tavily tools reuse the SSRF-pinned outbound HTTP wrapper (extended with a `safe_post`) and the existing `ToolSpec` registration. The `@observe` work follows the spec's redaction pattern: auto-capture for stages whose inputs/outputs carry no corpus body, manual `set_span_attributes` projection for the two stages that touch `Candidate.payload.body`. The CLI ingest wiring assembles real `Source` / `Enricher` / `MergeJournal` / `Corpus` / `LLMClient` / `EmbeddingClient` / `SlopClassifier` instances from `Config` + env + flags and dispatches to the existing `slopmortem.ingest.ingest()` orchestrator.

**Tech Stack:** Python 3.11+, async (`asyncio` + `anyio.CapacityLimiter`), `httpx` (via `safe_get` / `safe_post`), `lmnr-python` (`@observe` + `Laminar.set_span_attributes`), `qdrant-client`, `pydantic` v2, `typer`, `pytest` + `pytest-recording`. Inherits the existing dependency set — no new deps.

**Source spec:** [`docs/specs/2026-04-27-slopmortem-design.md`](../specs/2026-04-27-slopmortem-design.md). Relevant sections per task block. Source plan: [`docs/plans/2026-04-28-slopmortem-implementation.md`](2026-04-28-slopmortem-implementation.md) (this is its follow-up).

## Execution Strategy

**Selected: Sequential execution (matches the parent slopmortem plan's user-overridden Sequential strategy).**

Tasks run one at a time in the order listed below. None of the three carry-overs share files with each other, so file-ownership conflicts cannot arise; sequencing is purely about keeping the diff reviewable and the test suite green between tasks.

Order of execution:

1. **Task A**: Tavily synthesis tools (smallest blast radius; isolated to `tools_impl.py` + `http.py` extension).
2. **Task B**: `@observe` decorators on `extract_facets`, `retrieve`, `llm_rerank` (touches three stage files + adds one regression test; no behavioral change).
3. **Task C**: CLI ingest wiring (heaviest; replaces the stub in `cli.py:_run_ingest` with real dependency assembly and dispatches to the existing `ingest()` orchestrator).

Implementation uses `superpowers:executing-plans`: read this plan, work the next unchecked task, run its TDD steps in order, mark each step done as it's verified, request review after the task closes, then move on. No fan-out, no agent teams. The user dispatches a single `python-development:python-pro` subagent per task with a self-contained brief, mirroring the pattern that landed Tasks 7–11.

## Agent Assignments

All code tasks use `python-development:python-pro`. Same rationale as the parent plan: Python 3.14+, async, `uv`, `ruff`, Pydantic v2, `lmnr-python`, `httpx`. Exactly this stack.

| # | Task | Agent type | Domain |
|---|------|------------|--------|
| A | Tavily synthesis tools (`tavily_search`, `tavily_extract`) + `safe_post` | python-development:python-pro | Python |
| B | `@observe` decorators on the three retrieval/extraction stages + body-leak regression test | python-development:python-pro | Python |
| C | `slopmortem ingest` CLI wiring → real orchestrator | python-development:python-pro | Python |

---

## How to read this plan

Each task block has: **Files** (create / modify / test paths), **Spec refs** (line ranges in the design spec the implementer must read before starting), **Pre-flight** (anything to verify before coding), **TDD steps** (failing test → minimal impl → green), and **Verification** (commands and expected output).

Implementers should:

1. Read this task block.
2. Read the spec sections it references.
3. Run the TDD steps in order; do not batch.
4. Run the full sweep at the end of every task (`pytest`, `ruff check`, `ruff format --check`, `basedpyright`).
5. Flip `- [ ]` → `- [x]` after each step verifies.

uv is **not** on PATH in this environment. Use the project venv directly:

- `./.venv/bin/pytest`
- `./.venv/bin/ruff`
- `./.venv/bin/basedpyright`
- `./.venv/bin/python`

Do NOT run `git add` / `git commit` from inside an implementer subagent. The parent owns commit authorship (see `feedback_subagent_no_commits.md`). An external watcher may auto-commit edits anyway; treat as best-effort.

---

## Task A: Tavily synthesis tools

**Files:**
- Modify: `slopmortem/http.py` (add `safe_post` next to `safe_get`)
- Modify: `slopmortem/corpus/tools_impl.py` (replace `_tavily_search` / `_tavily_extract` `NotImplementedError` bodies with real impls; pass `Config` so the tools read `config.tavily_api_key`)
- Test: `tests/test_safe_post.py` (new — mirrors the existing `test_ssrf.py` patterns for `safe_get`)
- Test: `tests/test_tavily_tools.py` (new — tool-level tests with a mocked `safe_post`)

**Spec refs:** §Synthesis tool registry (lines 1011–1014), §Security (lines 1023–…), §Tavily call budget (line 1005), §Auth (line 207).

### Why a `safe_post` is needed

`slopmortem/http.py:safe_get` is the only sanctioned outbound HTTP path. Tavily's `/search` and `/extract` APIs are POST-only, so the existing helper does not fit. Three options were considered:

- **Extend `http.py` with `safe_post` mirroring `safe_get`'s DNS-pinned SSRF model.** Pros: same security guarantees, single owner of outbound HTTP, future-proof for any other POST adapter. Cons: small surface-area expansion in `http.py`. Auto-selected; no downsides compared to alternatives.
- Use `httpx.AsyncClient` directly inside the Tavily tool. Pros: zero `http.py` surface change. Cons: duplicates SSRF logic; violates the "safe_get is the only sanctioned outbound HTTP" invariant; future POST adapters will copy the duplicate.
- Reuse `safe_get` with Tavily's deprecated GET endpoints. Pros: zero surface change. Cons: Tavily's documented API is POST-first; falling back to GET ties this code to a deprecated path.

### Step-by-step

- [x] **Step A.1: Read the spec sections listed above.** No code yet.

- [x] **Step A.2: Write `safe_post` failing tests.**

Add `tests/test_safe_post.py`. Mirror the existing `tests/test_ssrf.py` pattern (which exercises `safe_get`'s scheme + DNS pinning). Cover at least:

```python
import pytest
from slopmortem.http import safe_post

@pytest.mark.asyncio
async def test_safe_post_rejects_non_http_scheme():
    with pytest.raises(ValueError, match="non-http"):
        await safe_post("file:///etc/passwd", json={})

@pytest.mark.asyncio
async def test_safe_post_rejects_loopback_via_dns_rebind(monkeypatch):
    # Same DNS-pinning test as test_ssrf.py:test_safe_get_rejects_loopback_dns_rebind,
    # adapted for safe_post's signature.
    ...

@pytest.mark.asyncio
async def test_safe_post_passes_json_body_through(httpx_mock):
    httpx_mock.add_response(json={"ok": True})
    resp = await safe_post("https://api.tavily.com/search", json={"query": "x"})
    assert resp.json() == {"ok": True}
```

- [x] **Step A.3: Run the tests; confirm they fail with "safe_post not defined".**

```
./.venv/bin/pytest tests/test_safe_post.py -v
```

- [x] **Step A.4: Implement `safe_post` in `slopmortem/http.py`.**

Lift the SSRF-pinning machinery from `safe_get` into a private `_resolve_and_validate(url) -> str` helper, then have both `safe_get` and `safe_post` call it. New public function:

```python
async def safe_post(
    url: str,
    *,
    json: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,  # noqa: ASYNC109
) -> httpx.Response:
    """POST *json* to *url* after enforcing scheme and DNS-pinned SSRF checks."""
    pinned_url = _resolve_and_validate(url)
    async with httpx.AsyncClient() as client:
        return await client.post(pinned_url, json=json, headers=headers, timeout=timeout)
```

Refactor `safe_get` to call `_resolve_and_validate` so the two helpers share one implementation of the DNS pin. Do not change `safe_get`'s public signature.

- [x] **Step A.5: Run `pytest tests/test_safe_post.py -v` + the existing `tests/test_ssrf.py` to confirm both helpers stay green.**

The existing `safe_get` tests must still pass after the refactor. If any test fails, the refactor introduced a regression. Fix it before moving on; do not paper over by skipping a test.

- [x] **Step A.6: Write the Tavily tool failing tests.**

Add `tests/test_tavily_tools.py`. The Tavily tools currently raise `NotImplementedError`; the tests describe the post-implementation contract. At minimum:

```python
from unittest.mock import AsyncMock
import httpx
import pytest
from slopmortem.corpus.tools_impl import _tavily_search, _tavily_extract

@pytest.mark.asyncio
async def test_tavily_search_calls_api_and_formats_results(monkeypatch):
    fake_resp = httpx.Response(
        200,
        json={
            "results": [
                {"title": "Co. shut down", "url": "https://example.com/a", "content": "..."},
                {"title": "Post-mortem", "url": "https://example.com/b", "content": "..."},
            ]
        },
    )
    mock_post = AsyncMock(return_value=fake_resp)
    monkeypatch.setattr("slopmortem.corpus.tools_impl.safe_post", mock_post)
    monkeypatch.setenv("TAVILY_API_KEY", "tv-test-key")
    out = await _tavily_search("acme failure", limit=2)
    # Result is a string the LLM can read; contains URLs and titles, no raw HTML.
    assert "example.com/a" in out
    assert "example.com/b" in out
    # Body uses Tavily's documented JSON shape.
    body = mock_post.call_args.kwargs["json"]
    assert body["query"] == "acme failure"
    assert body["max_results"] == 2
    assert body["api_key"] == "tv-test-key"

@pytest.mark.asyncio
async def test_tavily_search_raises_on_missing_api_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TAVILY_API_KEY"):
        await _tavily_search("x", limit=1)

@pytest.mark.asyncio
async def test_tavily_extract_calls_api_and_returns_text(monkeypatch):
    fake_resp = httpx.Response(
        200,
        json={"results": [{"url": "https://example.com/x", "raw_content": "extracted body"}]},
    )
    mock_post = AsyncMock(return_value=fake_resp)
    monkeypatch.setattr("slopmortem.corpus.tools_impl.safe_post", mock_post)
    monkeypatch.setenv("TAVILY_API_KEY", "tv-test-key")
    out = await _tavily_extract("https://example.com/x")
    assert "extracted body" in out

@pytest.mark.asyncio
async def test_tavily_extract_propagates_http_error(monkeypatch):
    fake_resp = httpx.Response(429, json={"detail": "rate limited"})
    monkeypatch.setattr(
        "slopmortem.corpus.tools_impl.safe_post", AsyncMock(return_value=fake_resp)
    )
    monkeypatch.setenv("TAVILY_API_KEY", "tv-test-key")
    with pytest.raises(httpx.HTTPStatusError):
        await _tavily_extract("https://example.com/x")
```

- [x] **Step A.7: Run the tests; confirm they fail.**

```
./.venv/bin/pytest tests/test_tavily_tools.py -v
```

Expected: all four tests fail with `NotImplementedError` (or import errors if `safe_post` import in the impl breaks first).

- [x] **Step A.8: Implement `_tavily_search` and `_tavily_extract` in `slopmortem/corpus/tools_impl.py`.**

```python
import os
from slopmortem.http import safe_post

_TAVILY_SEARCH_URL = "https://api.tavily.com/search"
_TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"


def _tavily_api_key() -> str:
    key = os.environ.get("TAVILY_API_KEY", "")
    if not key:
        msg = "TAVILY_API_KEY not set; --tavily-synthesis path is unavailable"
        raise RuntimeError(msg)
    return key


async def _tavily_search(q: str, limit: int = 5) -> str:
    """Search the live web via Tavily; return a compact text summary the LLM can read."""
    resp = await safe_post(
        _TAVILY_SEARCH_URL,
        json={"api_key": _tavily_api_key(), "query": q, "max_results": limit},
    )
    resp.raise_for_status()
    payload = resp.json()
    lines = []
    for hit in payload.get("results", [])[:limit]:
        title = hit.get("title", "(no title)")
        url = hit.get("url", "")
        snippet = (hit.get("content") or "")[:500]
        lines.append(f"- {title} — {url}\n  {snippet}")
    return "\n".join(lines) if lines else "(no results)"


async def _tavily_extract(url: str) -> str:
    """Fetch and extract the readable text of a single URL via Tavily."""
    resp = await safe_post(
        _TAVILY_EXTRACT_URL,
        json={"api_key": _tavily_api_key(), "urls": [url]},
    )
    resp.raise_for_status()
    payload = resp.json()
    results = payload.get("results", [])
    if not results:
        return ""
    return str(results[0].get("raw_content", ""))
```

The `api_key` is read from the env var rather than `Config` because the Tavily tools are passed bare to OpenRouter (not the full `Config`), and the existing `_set_corpus(corpus)` indirection would not extend cleanly to a `_set_config(config)` second binding. Reading from env at call time is the pattern `_get_post_mortem` / `_search_corpus` already break (those use a module-level `_corpus`); the env var read is acceptable here because `TAVILY_API_KEY` is the documented surface in the spec (line 207, line 1023). Document this in the function docstring.

- [x] **Step A.9: Run all the Tavily-tool tests; confirm they pass.**

```
./.venv/bin/pytest tests/test_tavily_tools.py -v
```

Expected: 4 passed.

- [x] **Step A.10: Run the full sweep.**

```
./.venv/bin/pytest tests/ -q
./.venv/bin/ruff check slopmortem tests
./.venv/bin/ruff format --check slopmortem tests
./.venv/bin/basedpyright slopmortem tests
```

Expected: all green; test count = 221 + 7 = 228.

### Out of scope for Task A

- The Tavily *enricher* (ingest-time, separate from synthesis tools). The `--tavily-enrich` flag stays a stub; Task C will reject it with a clear error message. A future plan adds the `TavilyEnricher` class.
- Per-call Tavily budget enforcement (≤2 calls per synthesis from spec line 1005). The synthesize stage's tool-use loop is the right place for that gate; Task A does not modify the synthesize stage. If the gate is missing today (likely is), file a follow-up; do not bundle.

---

## Task B: `@observe` decorators on the three deferred stages


**Files:**
- Modify: `slopmortem/stages/facet_extract.py` (add `@observe(name="stage.facet_extract")`)
- Modify: `slopmortem/stages/retrieve.py` (add `@observe(name="stage.retrieve")` + redacted output projection)
- Modify: `slopmortem/stages/llm_rerank.py` (add `@observe(name="stage.llm_rerank", ignore_inputs=["candidates"])` + redacted input projection)
- Test: `tests/test_observe_redaction.py` (new — Laminar exporter regression test, asserts no body strings appear in any captured span)

**Spec refs:** §Tracing (lines 914–924, especially line 919 on auto-capture and the `ignore_inputs` redaction pattern), §Security (lines 1023+).

### Why three stages have different decorators

`@observe` defaults to capturing every Pydantic input and output as span attributes. That captures fields recursively, so any path that contains a `Candidate` will leak `payload.body` (the full corpus text). The three stages' shapes:

| Stage | Inputs (auto-capture risk) | Outputs (auto-capture risk) | Decision |
|---|---|---|---|
| `extract_facets` | `text: str` (the user's pitch, no corpus body), `llm`, `model` | `Facets` (closed-enum strings, no body) | Plain `@observe(name="stage.facet_extract")`; auto-capture is safe |
| `retrieve` | `description, facets, corpus, embedding_client, cutoff_iso, strict_deaths, k_retrieve` (none carry body) | `list[Candidate]` (every element has `payload.body`) | `@observe(name="stage.retrieve")` + manual redacted projection of outputs via `Laminar.set_span_attributes`. **Use `ignore_outputs=True`** so the auto-capture doesn't undo the redaction. Re-attach `(canonical_id, score, name, facets, slop_score)` per candidate as a span attribute |
| `llm_rerank` | `candidates: list[Candidate]` (body), `pitch, llm, config, model` | `LlmRerankResult` (no body, just `(candidate_id, perspective_scores, rationale)`) | `@observe(name="stage.llm_rerank", ignore_inputs=["candidates"])` + manual redacted projection of `candidates` (canonical_ids + names) via `set_span_attributes`. Output auto-capture stays on |

The `synthesize` stage is already correctly decorated (`ignore_inputs=["candidate"]` per spec line 919) and is not touched by Task B.

### Step-by-step

- [ ] **Step B.1: Read spec lines 914–924 in full.** Especially the wording "matches top-level parameter names only". The `ignore_inputs` filter is `k in ignore_inputs` against `inspect.signature(func).parameters.keys()`, so it has to use the exact parameter name (`candidates`, not `retrieved`).

- [ ] **Step B.2: Verify lmnr-python's `@observe` API.**

Confirm `ignore_outputs=True` exists and behaves the way the spec implies. Inspect the installed package:

```
./.venv/bin/python -c "import inspect; from lmnr import observe; print(inspect.signature(observe))"
```

Expected: signature includes `ignore_inputs`, `ignore_outputs` (or equivalent). If `ignore_outputs` does NOT exist as a kwarg, the fallback is to capture `name` only (no inputs/outputs auto-captured) and re-attach everything manually. Document the choice in the stage's docstring.

- [ ] **Step B.3: Write the regression test (failing — neither file decorated yet).**

Add `tests/test_observe_redaction.py`. The test runs the full pipeline against fakes (lifting the pattern from `tests/test_pipeline_e2e.py`) but inserts a Laminar `InMemorySpanExporter` (or whatever `lmnr-python` ships as its in-memory exporter). After the run, the test scrapes every span attribute value and asserts no candidate `payload.body` substring appears anywhere.

```python
"""Regression test: corpus body never appears in Laminar-captured span attributes."""
from __future__ import annotations

import json
import pytest
from lmnr import Laminar
# Use whichever in-memory exporter lmnr-python exposes; if none, install
# opentelemetry-sdk's InMemorySpanExporter as a transport target.
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from slopmortem.budget import Budget
from slopmortem.llm.fake import FakeLLMClient
from slopmortem.llm.fake_embeddings import FakeEmbeddingClient
from slopmortem.models import InputContext
from slopmortem.pipeline import run_query

# Re-use the helpers already proven at tests/test_pipeline_e2e.py — copy
# _facets, _payload, _candidate, _build_canned, _FakeCorpus, _build_config,
# and _BODY_SENTINEL into this test file. Inline duplication is fine here;
# extracting a shared fixture is out of scope.

_BODY_SENTINEL = "ZZ-CANARY-CORPUS-BODY-DO-NOT-EXFILTRATE-ZZ"


async def test_no_corpus_body_in_laminar_spans(monkeypatch):
    exporter = InMemorySpanExporter()
    # Wire exporter into Laminar's tracer provider (the exact wiring depends
    # on lmnr-python's exporter API — do whatever the SDK documents).
    Laminar.initialize(project_api_key="test-key", _exporter=exporter)
    try:
        # Same setup as test_pipeline_e2e.py:test_full_pipeline_with_fake_clients,
        # but every Candidate's payload.body contains _BODY_SENTINEL.
        ...
        report = await run_query(...)
        assert report.candidates  # sanity

        # Drain the exporter and assert.
        spans = exporter.get_finished_spans()
        captured = json.dumps([dict(s.attributes or {}) for s in spans])
        assert _BODY_SENTINEL not in captured, (
            "corpus body leaked to Laminar span attributes"
        )
    finally:
        Laminar.shutdown()
```

If `lmnr-python` does not expose an exporter override hook, fall back to monkeypatching the OTel tracer provider directly via `opentelemetry.trace.set_tracer_provider`. Document the chosen wiring inline.

- [ ] **Step B.4: Run the test; confirm it fails.**

```
./.venv/bin/pytest tests/test_observe_redaction.py -v
```

Expected: FAIL. Body sentinel appears in span attributes (because `retrieve` and `llm_rerank` don't have any redaction yet, and `synthesize`'s `ignore_inputs=["candidate"]` only handles its own input, not the upstream stages' captures).

- [ ] **Step B.5: Decorate `extract_facets`.**

In `slopmortem/stages/facet_extract.py`, add the import and decorator. No redaction needed.

```python
from lmnr import observe

@observe(name="stage.facet_extract")
async def extract_facets(text: str, llm: LLMClient, model: str | None = None) -> Facets:
    ...
```

- [ ] **Step B.6: Decorate `retrieve` with output redaction.**

In `slopmortem/stages/retrieve.py`:

```python
from lmnr import Laminar, observe

@observe(name="stage.retrieve", ignore_outputs=True)
async def retrieve(*, description, facets, corpus, embedding_client, cutoff_iso, strict_deaths, k_retrieve) -> list[Candidate]:
    candidates = ...  # existing body
    Laminar.set_span_attributes({
        "candidates": [
            {
                "canonical_id": c.canonical_id,
                "score": c.score,
                "name": c.payload.name,
                "facets": c.payload.facets.model_dump(),
                "slop_score": c.payload.slop_score,
            }
            for c in candidates
        ],
    })
    return candidates
```

If lmnr-python does not expose `ignore_outputs`, use `@observe(name="stage.retrieve", ignore_inputs=[<every-param-name>], ignore_outputs=...)` or whatever the SDK supports. Write a one-line note in the docstring documenting the fallback.

- [ ] **Step B.7: Decorate `llm_rerank` with input redaction.**

In `slopmortem/stages/llm_rerank.py`:

```python
from lmnr import Laminar, observe

@observe(name="stage.llm_rerank", ignore_inputs=["candidates"])
async def llm_rerank(candidates, pitch, ...) -> LlmRerankResult:
    Laminar.set_span_attributes({
        "candidates_meta": [
            {"canonical_id": c.canonical_id, "name": c.payload.name}
            for c in candidates
        ],
    })
    ...  # existing body
```

- [ ] **Step B.8: Run the regression test; confirm it passes.**

```
./.venv/bin/pytest tests/test_observe_redaction.py -v
```

Expected: PASS. Sentinel does not appear in any captured span.

- [ ] **Step B.9: Run the existing pipeline e2e tests; confirm they still pass.**

```
./.venv/bin/pytest tests/test_pipeline_e2e.py -v
```

Expected: 8 passed (same as before; decorators are no-ops when Laminar is not initialized).

- [ ] **Step B.10: Run the full sweep.**

```
./.venv/bin/pytest tests/ -q
./.venv/bin/ruff check slopmortem tests
./.venv/bin/ruff format --check slopmortem tests
./.venv/bin/basedpyright slopmortem tests
```

Expected: all green; test count = 228 + 1 = 229.

### Out of scope for Task B

- Adding `@observe` to `LLMClient.complete` / `EmbeddingClient.embed` / `Corpus.query`. Those have manual spans per spec lines 920–922 and are already in place.
- Tightening the synthesize stage's redaction. Already correct per spec line 919.

---

## Task C: `slopmortem ingest` CLI wiring

**Files:**
- Modify: `slopmortem/cli.py` (replace `_run_ingest`'s flag-echoing body with real wiring; reuse `_build_deps` helper pattern, add an analogous `_build_ingest_deps`)
- Test: `tests/test_cli_ingest.py` (new — exercises the wiring via `runner.invoke(app, ["ingest", "--dry-run"])` with monkeypatched factories so no real Qdrant / ONNX / network is touched)

**Spec refs:** §Pipeline overview line 174 (one trace per CLI invocation), §Ingest orchestration (lines 240–268), §Slop filter at ingest (lines 250+), §Ingest concurrency (lines 28, 35), §Ingest CLI flags (line 281–287).

### Why this is heavier than the other two

The existing `slopmortem.ingest.ingest()` orchestrator at `slopmortem/ingest.py:660` already implements the whole pipeline; it just needs callers to assemble its dependencies. The CLI stub today only echoes flags; this task wires real instances. The wiring includes:

- `Sources`: `CuratedSource` (always-on), `HNAlgoliaSource` (always-on), `CrunchbaseCsvSource` (iff `--crunchbase-csv path`).
- `Enrichers`: `WaybackEnricher` (iff `--enrich-wayback`). `--tavily-enrich` is rejected with a clear `typer.Exit(1)` referencing "TavilyEnricher not implemented; deferred to a follow-up plan" — the flag stays in the surface for forward-compat but does not silently no-op.
- `MergeJournal`: existing class; takes a SQLite path (`config.journal_sqlite_path` if it exists, else `data/journal.sqlite` under repo root).
- `Corpus`: real `QdrantCorpus` via the same construction `_build_deps` already does for `query`. Refactor: extract a `_build_qdrant_corpus(config) -> QdrantCorpus` helper so both `query` and `ingest` use one path.
- `LLMClient`: same `OpenRouterClient` as `query`, but with the ingest budget cap (`max_cost_usd_per_ingest=15.00`) instead of the per-query cap.
- `EmbeddingClient`: same `OpenAIEmbeddingClient` as `query`.
- `Budget`: `Budget(cap_usd=config.max_cost_usd_per_ingest)`.
- `SlopClassifier`: production is `BinocularsSlopClassifier`; that class already exists in `slopmortem/ingest.py`. Construct it via its zero-arg constructor (it lazy-loads the ONNX model on first score call).

For `--reconcile`, `--reclassify`, `--list-review`: these are SEPARATE orchestration paths in the spec, NOT parameters of `ingest()`. They are out of scope for this task. The CLI should reject each of those flags with a `typer.Exit(1)` and a "not implemented in this iteration" message naming the follow-up ticket. The flags stay in the CLI surface so the user-facing surface does not regress; users get a clear error rather than a silent no-op.

### Pre-flight

- [ ] **Step C.0: Read `slopmortem/ingest.py:660-720` (`ingest()` orchestrator's full signature and docstring).** Confirm the dependency list. If new fields landed since this plan was written, propagate them in this task's wiring.

- [ ] **Step C.0a: Confirm `MergeJournal` constructor signature.**

```
grep -n "^class MergeJournal\|def __init__" slopmortem/corpus/journal.py | head -5
```

Use the result to pin the `MergeJournal(...)` line in Step C.4.

- [ ] **Step C.0b: Confirm whether `Config` has `journal_sqlite_path` or similar.**

```
grep -n "journal\|sqlite" slopmortem/config.py
```

If yes, use it. If no, default to `Path("data/journal.sqlite")` and document inline.

### Step-by-step

- [ ] **Step C.1: Write the CLI ingest test scaffolding (failing — wiring not in place).**

Add `tests/test_cli_ingest.py`:

```python
"""CLI tests for ``slopmortem ingest``: wiring assembled, orchestrator dispatched."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from typer.testing import CliRunner

from slopmortem.cli import app


def test_ingest_dry_run_dispatches_to_orchestrator(monkeypatch, tmp_path):
    """--dry-run path: real wiring assembled, ingest() called with dry_run=True."""
    fake_ingest = AsyncMock(return_value=MagicMock(dry_run=True, processed=0))
    monkeypatch.setattr("slopmortem.cli.ingest", fake_ingest)
    # Block real Qdrant / OpenRouter / OpenAI / sqlite construction:
    monkeypatch.setattr(
        "slopmortem.cli._build_ingest_deps",
        lambda config, post_mortems_root: (
            MagicMock(name="llm"),
            MagicMock(name="embed"),
            MagicMock(name="corpus"),
            MagicMock(name="budget"),
            MagicMock(name="journal"),
            MagicMock(name="slop"),
        ),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["ingest", "--dry-run", "--post-mortems-root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert fake_ingest.await_count == 1
    kwargs = fake_ingest.await_args.kwargs
    assert kwargs["dry_run"] is True
    assert kwargs["force"] is False
    assert kwargs["post_mortems_root"] == tmp_path


def test_ingest_tavily_enrich_rejected(tmp_path):
    """--tavily-enrich is deferred; CLI must error out cleanly, not silently no-op."""
    runner = CliRunner()
    result = runner.invoke(
        app, ["ingest", "--tavily-enrich", "--post-mortems-root", str(tmp_path)]
    )
    assert result.exit_code != 0
    assert "Tavily" in result.output
    assert "deferred" in result.output


@pytest.mark.parametrize("flag", ["--reconcile", "--reclassify", "--list-review"])
def test_ingest_deferred_flags_rejected(flag, tmp_path):
    """--reconcile / --reclassify / --list-review are separate paths, not in this task."""
    runner = CliRunner()
    result = runner.invoke(app, ["ingest", flag, "--post-mortems-root", str(tmp_path)])
    assert result.exit_code != 0
    assert flag.lstrip("-") in result.output.lower() or "deferred" in result.output


def test_ingest_with_crunchbase_csv_appends_source(monkeypatch, tmp_path):
    """When --crunchbase-csv is given, the sources list includes CrunchbaseCsvSource."""
    captured = {}
    async def fake_ingest(*, sources, **kwargs):
        captured["sources"] = sources
        return MagicMock(dry_run=True, processed=0)

    monkeypatch.setattr("slopmortem.cli.ingest", fake_ingest)
    monkeypatch.setattr(
        "slopmortem.cli._build_ingest_deps",
        lambda config, post_mortems_root: (
            MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()
        ),
    )
    csv = tmp_path / "cb.csv"
    csv.write_text("name,description\n", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "ingest",
            "--dry-run",
            "--crunchbase-csv",
            str(csv),
            "--post-mortems-root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    source_classnames = [type(s).__name__ for s in captured["sources"]]
    assert "CrunchbaseCsvSource" in source_classnames
    assert "CuratedSource" in source_classnames
    assert "HNAlgoliaSource" in source_classnames
```

- [ ] **Step C.2: Run the tests; confirm they fail.**

```
./.venv/bin/pytest tests/test_cli_ingest.py -v
```

Expected: all four tests fail. The first three fail because `_build_ingest_deps` doesn't exist yet and the stub body still echoes flags rather than dispatching. The fourth fails for the same reason.

- [ ] **Step C.3: Add `_build_ingest_deps` helper to `slopmortem/cli.py`.**

Add next to `_build_deps`:

```python
def _build_ingest_deps(
    config: Config,
    post_mortems_root: Path,
) -> tuple[LLMClient, EmbeddingClient, Corpus, Budget, MergeJournal, SlopClassifier]:
    """Construct the ingest-side wiring: LLM / embed / corpus / budget / journal / classifier.

    Mirrors :func:`_build_deps` but uses ``max_cost_usd_per_ingest`` for the
    budget cap and additionally constructs a :class:`MergeJournal` and the
    production :class:`BinocularsSlopClassifier`.
    """
    from qdrant_client import AsyncQdrantClient  # noqa: PLC0415
    from slopmortem.corpus.journal import MergeJournal  # noqa: PLC0415
    from slopmortem.ingest import BinocularsSlopClassifier  # noqa: PLC0415

    budget = Budget(cap_usd=config.max_cost_usd_per_ingest)

    openrouter_sdk = AsyncOpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url=config.openrouter_base_url,
    )
    llm = OpenRouterClient(
        sdk=openrouter_sdk,
        budget=budget,
        model=config.model_facet,  # ingest uses the cheap model
    )

    openai_sdk = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
    embedder = OpenAIEmbeddingClient(
        sdk=openai_sdk,
        budget=budget,
        model=config.embed_model_id,
    )

    qdrant_host = os.environ.get("QDRANT_HOST", "localhost")
    qdrant_port = int(os.environ.get("QDRANT_PORT", "6333"))
    qdrant_client = AsyncQdrantClient(host=qdrant_host, port=qdrant_port)
    corpus = QdrantCorpus(
        client=qdrant_client,
        collection=os.environ.get("QDRANT_COLLECTION", "slopmortem"),
        post_mortems_root=post_mortems_root,
        facet_boost=config.facet_boost,
        rrf_k=config.rrf_k,
    )

    journal_path = Path(
        os.environ.get("MERGE_JOURNAL_PATH", post_mortems_root.parent / "journal.sqlite")
    )
    journal = MergeJournal(journal_path)

    classifier = BinocularsSlopClassifier()

    return llm, embedder, corpus, budget, journal, classifier
```

If `MergeJournal`'s constructor signature differs from `MergeJournal(path)`, adjust per Step C.0a's grep.

- [ ] **Step C.4: Replace `_run_ingest`'s body with real wiring.**

Replace `slopmortem/cli.py:_run_ingest` (the existing body that just echoes flags) with:

```python
async def _run_ingest(  # noqa: PLR0913
    *,
    dry_run: bool,
    force: bool,
    reconcile: bool,
    reclassify: bool,
    list_review: bool,
    crunchbase_csv: Path | None,
    enrich_wayback: bool,
    tavily_enrich: bool,
    post_mortems_root: Path,
) -> None:
    """Async impl behind ``slopmortem ingest``. Resolves wiring then dispatches."""
    if tavily_enrich:
        typer.echo(
            "--tavily-enrich is deferred to a follow-up plan; "
            "TavilyEnricher is not implemented in this iteration.",
            err=True,
        )
        raise typer.Exit(code=1)
    if reconcile or reclassify or list_review:
        flag_name = (
            "reconcile" if reconcile
            else "reclassify" if reclassify
            else "list-review"
        )
        typer.echo(
            f"--{flag_name} is a separate orchestration path "
            "deferred to a follow-up plan; not implemented in this iteration.",
            err=True,
        )
        raise typer.Exit(code=1)

    config = load_config()
    _maybe_init_tracing(config)
    llm, embedder, corpus, budget, journal, classifier = _build_ingest_deps(
        config, post_mortems_root
    )
    _set_corpus(corpus)

    sources: list[Source] = [CuratedSource(), HNAlgoliaSource()]
    if crunchbase_csv is not None:
        sources.append(CrunchbaseCsvSource(csv_path=crunchbase_csv))

    enrichers: list[Enricher] = []
    if enrich_wayback:
        enrichers.append(WaybackEnricher())

    result = await ingest(
        sources=sources,
        enrichers=enrichers,
        journal=journal,
        corpus=corpus,
        llm=llm,
        embed_client=embedder,
        budget=budget,
        slop_classifier=classifier,
        config=config,
        post_mortems_root=post_mortems_root,
        dry_run=dry_run,
        force=force,
    )
    typer.echo(f"slopmortem ingest result: {result}")
```

Add the necessary imports at the top of `cli.py`:

```python
from slopmortem.corpus.sources.curated import CuratedSource
from slopmortem.corpus.sources.crunchbase_csv import CrunchbaseCsvSource
from slopmortem.corpus.sources.hn_algolia import HNAlgoliaSource
from slopmortem.corpus.sources.wayback import WaybackEnricher
from slopmortem.ingest import ingest
```

If the actual class names differ (run `grep "^class " slopmortem/corpus/sources/*.py` to confirm), substitute correctly.

- [ ] **Step C.5: Run the CLI ingest tests; confirm they pass.**

```
./.venv/bin/pytest tests/test_cli_ingest.py -v
```

Expected: 6 passed (the four written + two `test_ingest_deferred_flags_rejected` parametrized cases — actually 3 parametrized cases = 6 total).

- [ ] **Step C.6: Update the `cli.py` module docstring.**

The current docstring at `slopmortem/cli.py` line 15-17 says "The `ingest` command is unchanged from the v1 5b stub — production wiring lands in a follow-up." Update to reflect that the stub is now real:

```python
"""...
The ``ingest`` command assembles real :class:`Source` / :class:`Enricher`
/ :class:`MergeJournal` / :class:`Corpus` / :class:`LLMClient` /
:class:`EmbeddingClient` / :class:`SlopClassifier` instances from
:class:`Config` and env vars and dispatches to
:func:`slopmortem.ingest.ingest`. The ``--reconcile``, ``--reclassify``,
``--list-review``, and ``--tavily-enrich`` flags are deferred to a
follow-up plan and currently exit non-zero with a clear message.
"""
```

- [ ] **Step C.7: Run the full test suite.**

```
./.venv/bin/pytest tests/ -q
./.venv/bin/ruff check slopmortem tests
./.venv/bin/ruff format --check slopmortem tests
./.venv/bin/basedpyright slopmortem tests
```

Expected: all green; test count = 229 + 6 = 235.

- [ ] **Step C.8: Smoke-run the CLI surface (no real network).**

Verify the CLI surface is wired correctly:

```
./.venv/bin/python -m slopmortem.cli ingest --help
```

Expected: prints the typer help with all flags. Don't run `slopmortem ingest` for real here — that requires Qdrant + env keys and is part of the integration review, not this plan.

### Out of scope for Task C

- `--reconcile` orchestration path. Per spec, this walks the journal and corpus to detect / repair drift; it is a separate pipeline from `ingest()`. Follow-up.
- `--reclassify` orchestration path. Re-runs the slop classifier on quarantined docs; separate pipeline. Follow-up.
- `--list-review` orchestration path. Prints the `pending_review` queue; depends on the `pending_review` table being populated by entity resolution (already implemented) but needs a separate read-side query. Follow-up.
- `--tavily-enrich` enricher. Depends on a `TavilyEnricher` class that does not exist yet; lifting the synthesis-time Tavily tools (Task A) does not give us the ingest-time enricher for free, because the ingest flow's enricher contract is different. Follow-up.
- Live smoke run against real Qdrant + OpenRouter + OpenAI. That belongs in the parent plan's "Final integration review" section.

---

## Final verification (after all three tasks land)

- [ ] **Run the full sweep one more time.**

```
./.venv/bin/pytest tests/ -v
./.venv/bin/ruff check slopmortem tests
./.venv/bin/ruff format --check slopmortem tests
./.venv/bin/basedpyright slopmortem tests
./.venv/bin/python -m slopmortem.evals.runner --dataset tests/evals/datasets/seed.jsonl --baseline tests/evals/baseline.json
```

Expected: all green; test count ≥ 235; eval runner exits 0.

- [ ] **Confirm the spec's invariants still hold.**

```
grep -RIn "1536\|3072" slopmortem/ --include="*.py" | grep -v "openai_embeddings.py\|test_"
```

Expected: empty (vector dims still only named in `openai_embeddings.py`).

```
grep -RIn "NotImplementedError" slopmortem/corpus/tools_impl.py
```

Expected: empty (Tavily tools no longer raise).

```
grep -RIn "production wiring.*follow-up\|stub" slopmortem/cli.py
```

Expected: empty (CLI ingest no longer a stub).

- [ ] **Re-confirm the body-leak regression.**

```
./.venv/bin/pytest tests/test_observe_redaction.py -v
```

Expected: PASS (no body sentinel in any captured span).

---

## What this plan deliberately does NOT cover

- The parent plan's "Final integration review" section: Qdrant smoke, `make smoke-live`, end-to-end happy path, two-stage code review. Those are user-driven and require live infrastructure; they remain on the parent plan's TODO list.
- TavilyEnricher (ingest-time). Defer to a follow-up plan if the user wants `--tavily-enrich` to actually work.
- `--reconcile` / `--reclassify` / `--list-review` ingest paths. Defer to a follow-up plan.
- Per-call Tavily synthesis budget (≤2 calls per synthesis from spec line 1005). If the synthesize stage's tool-use loop does not enforce this today, file a follow-up; do not bundle into Task A.
