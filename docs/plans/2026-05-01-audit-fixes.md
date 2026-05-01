# Audit fixes — 7 validated bugs from 2026-05-01 review

> **For agentic workers:** Steps use checkbox (`- [ ]`) syntax — these are **persistent durable state**, not visual decoration. The executor edits the plan file in place: `- [ ]` → `- [x]` the instant a step verifies, before moving on. On resume, the executor scans existing `- [x]` marks and skips them — these steps are NOT redone.

**Goal:** Fix 7 bugs surfaced by the 2026-05-01 audit and validated by a 5-agent parallel investigation. Each was confirmed at high confidence with file:line evidence. Fixes restore two load-bearing CLAUDE.md guarantees (injection short-circuit, config precedence), close one budget-cap gap, add the missing observability around the min-similarity cascade, and patch three lower-impact correctness bugs in ingest and date math.

**Why (per-task evidence, validated):**

- **P1 #1 — Injection short-circuit not implemented end-to-end.** `synthesize.py:129` detects `_INJECTION_MARKER` and emits a Laminar event but does not flag the returned `Synthesis`. `models.py:130-149` `Synthesis` has no `injection_detected` field. `consolidate_risks.py:60-74` builds the lessons-only payload for the consolidator prompt — `where_diverged` is dropped, so the second LLM has to re-detect injection from lessons alone. CLAUDE.md's load-bearing claim ("the stage flips injection_detected=True and consolidate_risks short-circuits") is false today.
- **P1 #2 — Docs disagree with code on config precedence.** `config.py:117-123` returns `(init, env, dotenv, toml, secrets)` so env beats both TOML files at runtime, and within the toml source `slopmortem.local.toml` is listed second (`config.py:23, 113`) so it wins over `slopmortem.toml`. **This is the intended precedence** (env > local.toml > slopmortem.toml — standard 12-factor). What's wrong is the documentation: `config.py:18` says "TOML overrides env", `config.py:110` says "TOML wins over env at runtime", `README.md:62` says ".local.toml wins over env too", and `CLAUDE.md:32` lists "local.toml → env vars → .env → slopmortem.toml" as the precedence. All four claims are false. Fix is docs-only.
- **P2 #3 — Budget cap not enforced for OpenRouter.** `openrouter.py:192-198` settles cost with a literal string id and never reserves; `Budget.settle` (`budget.py:39-43`) only credits `spent_usd`. Only `Budget.reserve` raises (`budget.py:34`), and the only callers are `openai_embeddings.py:94` and `fastembed_client.py:94` (the latter is a zero-amount symmetry call). Pipeline-level `BudgetExceededError` handler (`pipeline.py:331`) effectively only fires for embedding work. Concurrent synth fan-out can blow past `cap_usd` arbitrarily far.
- **P2 #4 — `min_similarity_score` cascade is invisible.** `pipeline.py:286` and `:318` filter survivors with no log, no counter, no Report field. `PipelineMeta.filtered_pre_synth` (`models.py:264`) only counts pre-synth drops; the post-synth drop is not recorded anywhere. `render.py:183-184` empty-result banner can't distinguish "filter ate everything" from "no comparables in corpus."
- **P2 #5 — `mark_complete` runs with zero chunks.** `ingest.py:783-791` does `_ = await _embed_and_upsert(...)`, discarding the chunk count. `chunk_markdown` returns `[]` whenever `cl100k_base` tokenizes to zero (whitespace, control chars, tokenizer-empty bodies). `mark_complete` at `ingest.py:795-802` then writes a "complete" journal row for a canonical_id with zero Qdrant points. Reconcile drift class (a) catches this only retroactively.
- **P3 #6 — `delete_chunks_for_canonical` doesn't exist on `QdrantCorpus`.** `cli.py:707-711` casts to satisfy the Protocol and acknowledges the gap. `ingest.py:769-771` wraps the call in `contextlib.suppress(Exception)` — so every re-merge in prod silently swallows an `AttributeError`. Combined with deterministic chunk point IDs (`uuid5(NAMESPACE_URL, f"{canonical_id}:{chunk_idx}")` at `ingest.py:605`), shrink-to-shorter ingests leak orphaned higher-index chunks the retriever still hits. 100% leak rate, not an edge case.
- **P3 #7 — `cutoff_iso` leap-year drift.** `pipeline.py:46, 120` uses `timedelta(days=365 * years)`. Drifts ~1 day per 4 years. Low impact today (failure dates are year-granular per `_date_from_year` in `ingest.py:580-583`), but locks in silent off-by-1 the moment day-granular dates land.

**Tech Stack:** Python 3.13, anyio, pydantic v2, pydantic-settings, pytest (asyncio_mode=auto, xdist), basedpyright (strict), qdrant-client, python-dateutil.

## Priority

| Task | Pri | Type | Impact if skipped |
|---|---|---|---|
| **Task 1** Injection propagation | P1 | Correctness + security contract | Poisoned lessons reach `TopRisks`. CLAUDE.md guarantee stays broken. |
| **Task 2** Align config precedence docs with code | P1 | Documented contract | Users follow CLAUDE.md/README and put overrides in `local.toml` expecting them to beat env; they don't. Docs-only fix. |
| **Task 3** OpenRouter budget cap | P2 | Cost ceiling | Runaway runs blow past `cap_usd`. No bound on damage. |
| **Task 4** min_similarity observability | P2 | Operability | Empty Report indistinguishable between "threshold too high" and "corpus empty." |
| **Task 5** Zero-chunk guard | P2 | Corpus integrity | Journal "complete" rows with no Qdrant points; reconcile catches retroactively. |
| **Task 6** delete_chunks impl + narrow handling | P3 | Corpus integrity | 100% leak on shrink-to-shorter re-ingest. Orphans served at query time. |
| **Task 7** Leap-year cutoff fix | P3 | Latent correctness | Negligible today; matters when day-granular dates land. |

## Execution Strategy

**Sequential, single session, one task at a time.** Per project preference: do not parallelize tasks, do not dispatch parallel subagents for execution. Each task lists explicit **CREATE / MODIFY / DELETE** files. Stay within that list — no tangential dep bumps, refactors, or "small wins" outside the listed scope.

After each task: run targeted tests with `just test -k <pattern>`, confirm green, mark steps `[x]`, commit with terse subject (`fix`, `upd`, etc.) before starting the next task. Do not batch commits across tasks.

---

## Task 1 — Inject `injection_detected` flag end-to-end

**Files:**
- Modify: `slopmortem/models.py`
- Modify: `slopmortem/stages/synthesize.py`
- Modify: `slopmortem/stages/consolidate_risks.py`
- Modify: `CLAUDE.md` (correct the load-bearing claim once the contract holds)
- Create: `tests/stages/test_injection_propagation.py`
- Modify: `tests/stages/test_synthesize_injection_defense.py` (extend to assert the new flag)

**Decision:** Add a field to `Synthesis`. Single source of truth, type-checked, plumbed cleanly. Sidecar dict and string-inspection alternatives were rejected.

**Pros / Cons:**
- Pros: type-safe; `where_diverged` stays human-readable for `render.py`; no stringly-typed coupling between stages.
- Cons: none material. Cassettes capture LLM responses (not pydantic objects), and a search of `tests/fixtures/cassettes/` confirmed there's nothing to refresh.

**Steps:**

- [x] **Step 1: Add field to `Synthesis`.**

In `slopmortem/models.py`, insert `injection_detected: bool = False` **after** the existing `sources: list[str]` at line 149 (the last field in the class).

Update the `from_llm` classmethod (current signature at line 152: `from_llm(cls, llm_synth, *, founding_date, failure_date, sources)`) to accept and forward the flag. **Preserve the existing parameter order — `founding_date` comes before `failure_date`:**

```python
@classmethod
def from_llm(
    cls,
    llm_synth: LLMSynthesis,
    *,
    founding_date: date | None,
    failure_date: date | None,
    sources: list[str],
    injection_detected: bool = False,
) -> Synthesis:
    ...
    return cls(
        ...,
        injection_detected=injection_detected,
    )
```

(Note: `TopRisks.injection_detected` already exists at `models.py:291`, and the LLM-side short-circuit in `consolidate_risks.py:102-104` is already wired. Only the **synthesis-side** propagation is missing — that is what this task adds.)

- [x] **Step 2: Set the flag at the marker site.**

In `slopmortem/stages/synthesize.py:129-137`, change:

```python
if llm_parsed.where_diverged.strip() == _INJECTION_MARKER:
    _emit_event(SpanEvent.PROMPT_INJECTION_ATTEMPTED)

return Synthesis.from_llm(
    llm_parsed,
    founding_date=...,
    failure_date=...,
    sources=...,
)
```

to:

```python
injection_detected = llm_parsed.where_diverged.strip() == _INJECTION_MARKER
if injection_detected:
    _emit_event(SpanEvent.PROMPT_INJECTION_ATTEMPTED)

return Synthesis.from_llm(
    llm_parsed,
    founding_date=...,
    failure_date=...,
    sources=...,
    injection_detected=injection_detected,
)
```

- [x] **Step 3: Short-circuit in `consolidate_risks`.**

In `slopmortem/stages/consolidate_risks.py`, before the existing `if not syntheses: return TopRisks()` at line 54, add:

```python
if any(s.injection_detected for s in syntheses):
    _emit_event(SpanEvent.PROMPT_INJECTION_ATTEMPTED)
    return TopRisks(risks=[], injection_detected=True)
```

(`_emit_event` is already imported in this file.)

Fail-closed posture: any tainted synthesis short-circuits the whole consolidator. Per CLAUDE.md contract.

- [x] **Step 4: Write end-to-end propagation test.**

Create `tests/stages/test_injection_propagation.py`:

```python
"""End-to-end propagation: synthesize marker → consolidate short-circuits.

Closes the contract gap CLAUDE.md describes as load-bearing: when synthesize
detects _INJECTION_MARKER, consolidate_risks must return empty TopRisks
without consulting the consolidator LLM.
"""

import pytest

from slopmortem.llm.fake import FakeLLMClient
from slopmortem.models import Synthesis, TopRisks
from slopmortem.stages.consolidate_risks import consolidate_risks
# Build minimal Synthesis fixtures (use existing test helpers if present).

async def test_consolidate_short_circuits_on_injected_synthesis(...):
    tainted = Synthesis(..., injection_detected=True)
    clean = Synthesis(..., injection_detected=False)
    # FakeLLMClient API: positional fields are `canned` (Mapping) + `default_model` (str).
    # Empty `canned` doubles as a negative assertion: if the consolidator LLM is
    # reached, fake.py raises NoCannedResponseError. Combined with len(calls)==0,
    # this proves no LLM call happened.
    fake_llm = FakeLLMClient(canned={}, default_model="anthropic/claude-haiku-4-5")
    result = await consolidate_risks(
        [tainted, clean],
        pitch="...",
        llm=fake_llm,
        config=...,
        model="...",
        max_tokens=512,
    )
    assert result == TopRisks(risks=[], injection_detected=True)
    assert len(fake_llm.calls) == 0  # short-circuit before LLM
```

(`FakeLLMClient` exposes `calls: list[_Call]`, not a `call_count` property — see `slopmortem/llm/fake.py:71-72, 110`.)

Cover three cases:
1. All clean → consolidator runs (existing path, regression check).
2. One tainted, rest clean → short-circuit, no LLM call.
3. All tainted → short-circuit, no LLM call.

Use existing fixtures from `tests/stages/test_consolidate_risks.py` for `Synthesis` construction.

- [x] **Step 5: Extend synthesize injection test.**

In `tests/stages/test_synthesize_injection_defense.py:107-129`, add an assertion that the returned `Synthesis` has `injection_detected=True` when the LLM emits the marker, and `False` otherwise.

- [x] **Step 6: Update CLAUDE.md.**

In `CLAUDE.md`, the bullet currently reads:

> **Injection marker** (`slopmortem/stages/synthesize.py`): when the synthesis LLM emits `where_diverged == "prompt_injection_attempted"` (compared against `_INJECTION_MARKER` at `synthesize.py:129`), the stage flips `injection_detected=True` and `consolidate_risks` short-circuits to an empty risk list. Don't normalize the marker string away in prompts or post-processing.

Leave wording mostly intact — it's now true. Just confirm the line number reference and tighten if needed.

- [x] **Step 7: Verify.** `just test -k injection && just typecheck && just lint`. No cassette refresh expected.

---

## Task 2 — Align config precedence docs with actual code behavior

The intended (and implemented) precedence is **env > `slopmortem.local.toml` > `slopmortem.toml`** — standard 12-factor. The code is correct; four pieces of documentation are wrong.

**Files:**
- Modify: `slopmortem/config.py` (two docstrings)
- Modify: `CLAUDE.md` (line 32 precedence claim)
- Modify: `README.md` (line 62 ".local.toml wins over env too" claim)
- Modify: `tests/test_config.py` (or create — add precedence-locking tests)

**Steps:**

- [x] **Step 1: Fix `Config` class docstring at `config.py:18`.**

Currently: `"""All knobs slopmortem reads at startup. TOML overrides env, env overrides defaults."""`

Change to: `"""All knobs slopmortem reads at startup. Env overrides TOML; within TOML, local.toml overrides slopmortem.toml; built-in defaults are lowest."""`

- [x] **Step 2: Fix `settings_customise_sources` docstring at `config.py:110`.**

Currently: `"""Wire TOML sources after env and before secrets so TOML wins over env at runtime."""`

Change to: `"""Wire TOML below env+dotenv so env wins (12-factor). Within toml_file=("slopmortem.toml", "slopmortem.local.toml"), pydantic-settings applies the second file last, so local.toml wins over the tracked defaults."""`

- [x] **Step 3: Fix `CLAUDE.md` line 32.**

Currently: `Precedence (highest wins): slopmortem.local.toml → env vars → .env → slopmortem.toml (tracked defaults).`

Change to: `Precedence (highest wins): env vars → .env → slopmortem.local.toml → slopmortem.toml (tracked defaults).` Add a clarifying sentence: "Env always wins. Personal overrides go in `slopmortem.local.toml`; if you also set the same key in env, env beats it."

- [x] **Step 4: Fix `README.md` line 62.**

Currently: `... the loader reads both from the current working directory and .local.toml wins. .local.toml is gitignored. Env vars (and .env) also override the tracked defaults, but .local.toml wins over env too, so it's the one knob to reach for.`

The "but `.local.toml` wins over env too" half is wrong. Change to reflect the actual behavior: `slopmortem.local.toml` overrides `slopmortem.toml` (tracked defaults), and env vars (and `.env`) override both. Use `.local.toml` for personal config; export an env var when you want a one-off override on top.

- [x] **Step 5: Lock precedence with tests.**

Add to `tests/test_config.py` (verify the file exists; create if not):

```python
def test_env_overrides_local_toml(monkeypatch, tmp_path):
    """Env vars beat slopmortem.local.toml — standard 12-factor."""
    (tmp_path / "slopmortem.toml").write_text('max_cost_usd_per_query = 1.0\n')
    (tmp_path / "slopmortem.local.toml").write_text('max_cost_usd_per_query = 2.0\n')
    monkeypatch.setenv("MAX_COST_USD_PER_QUERY", "5.0")
    monkeypatch.chdir(tmp_path)
    cfg = load_config()
    assert cfg.max_cost_usd_per_query == 5.0  # env wins

def test_local_toml_overrides_main_toml(monkeypatch, tmp_path):
    """slopmortem.local.toml must beat slopmortem.toml when both exist and env is unset."""
    (tmp_path / "slopmortem.toml").write_text('max_cost_usd_per_query = 1.0\n')
    (tmp_path / "slopmortem.local.toml").write_text('max_cost_usd_per_query = 2.0\n')
    monkeypatch.delenv("MAX_COST_USD_PER_QUERY", raising=False)
    monkeypatch.chdir(tmp_path)
    cfg = load_config()
    assert cfg.max_cost_usd_per_query == 2.0  # local.toml wins over tracked toml

def test_dotenv_overrides_local_toml(monkeypatch, tmp_path):
    """`.env` beats slopmortem.local.toml (env tier > toml tier)."""
    (tmp_path / "slopmortem.local.toml").write_text('max_cost_usd_per_query = 2.0\n')
    (tmp_path / ".env").write_text('MAX_COST_USD_PER_QUERY=7.0\n')
    monkeypatch.delenv("MAX_COST_USD_PER_QUERY", raising=False)
    monkeypatch.chdir(tmp_path)
    cfg = load_config()
    assert cfg.max_cost_usd_per_query == 7.0
```

Use `max_cost_usd_per_query` (real field at `config.py:35`); `cap_usd` does not exist on `Config`.

- [x] **Step 6: Verify.** `just test -k config && just typecheck && just lint`.

---

## Task 3 — Enforce OpenRouter budget cap (gate + post-settle raise)

**Files:**
- Modify: `slopmortem/budget.py` (`BudgetExceededError` is defined here, not in `errors.py` — preserve the existing import shape across callers)
- Modify: `slopmortem/llm/openrouter.py`
- Modify: `tests/test_budget.py` (exists — extend it; expect to update `test_reserve_settle_under_gather` since it currently relies on `settle` not raising)
- Modify: `tests/llm/test_openrouter_unit.py` (the actual file — there's no `test_openrouter.py`)

**Decision:** Approach C — pre-call gate in the OpenRouter client + raise from `Budget.settle` when settled spend exceeds cap. No estimator-based reserve (rejected: token-count estimates pre-call are imprecise; maintenance burden not worth the marginal precision). Concurrent fan-out tail overshoot is accepted as a documented limitation (the gate + post-settle raise stops further calls; in-flight calls land their cost).

**Pros / Cons:**
- Pros: minimal code, no per-model price table maintenance beyond what `_compute_cost` already does, makes `cap_usd` actually bound runaway loops.
- Cons: not a strict hard cap under fan-out — N parallel calls in flight when one of them pushes over the cap will all settle. Documented in code comment.

**Steps:**

- [ ] **Step 1: Make `Budget.settle` raise on over-cap.**

In `slopmortem/budget.py:39-43`, change:

```python
async def settle(self, reservation_id: str, actual_usd: float) -> None:
    """Drop the reservation and credit *actual_usd* against ``spent_usd``."""
    async with self.lock:
        self.reserved.pop(reservation_id, None)
        self.spent_usd += actual_usd
```

to:

```python
async def settle(self, reservation_id: str, actual_usd: float) -> None:
    """Drop the reservation, credit *actual_usd*, raise if spent exceeds cap.

    Raising after the fact still bounds total spend: the call that pushed over
    is paid for, but no further call gets past the pre-call gate or a future
    reserve(). Concurrent fan-out can briefly run with multiple in-flight
    calls past the cap; this is documented and accepted.
    """
    async with self.lock:
        self.reserved.pop(reservation_id, None)
        self.spent_usd += actual_usd
        if self.spent_usd > self.cap_usd:
            msg = f"spent {self.spent_usd:.4f} > cap {self.cap_usd:.4f}"
            raise BudgetExceededError(msg)
```

- [ ] **Step 2: Add a pre-call gate in OpenRouter client.**

In `slopmortem/llm/openrouter.py`, locate the start of the `complete` method (the entry point — find with `grep -n "async def complete" slopmortem/llm/openrouter.py`). Before issuing the request, add:

```python
if self._budget.remaining <= 0.0:
    msg = f"budget exhausted: remaining {self._budget.remaining:.4f}"
    raise BudgetExceededError(msg)
```

This makes runaway loops stop. Cheap O(1) check.

- [ ] **Step 3: Update the inline comment that documented the gap.**

At `slopmortem/llm/openrouter.py:192-198`, replace the multi-line comment about "budget cap isn't enforced here" with a tighter note. Cost is read from `response.usage.cost` (line 143) — there is no `_compute_cost` helper. Suggested wording:

```python
# OpenRouter returns the cost on response.usage.cost; we settle that figure
# without a prior reserve (true cost is unknown until usage lands). Budget.settle
# now raises when spent_usd > cap_usd, and the pre-call gate above stops further
# calls. Tail overshoot from concurrent fan-out is bounded by
# N_synthesize × per-call cost.
```

- [ ] **Step 4: Tests.**

`BudgetExceededError` is exported from `slopmortem.budget`, not `slopmortem.errors` — match existing import sites: `from slopmortem.budget import Budget, BudgetExceededError`.

In `tests/test_budget.py` (file exists), add:

```python
async def test_settle_raises_when_spent_exceeds_cap():
    b = Budget(cap_usd=1.0)
    await b.settle("x", 0.5)
    with pytest.raises(BudgetExceededError):
        await b.settle("y", 0.6)
    assert b.spent_usd == 1.1  # both settled before raise

async def test_settle_does_not_raise_at_cap():
    b = Budget(cap_usd=1.0)
    await b.settle("x", 1.0)
    assert b.spent_usd == 1.0  # equal is OK
```

**Re-check `test_reserve_settle_under_gather` in the same file.** Current shape: `cap_usd=1.00`, three concurrent `call(0.30, 0.20)` settles totaling 0.60 — stays well under cap, so the new over-cap raise will not fire and the test passes unchanged. No edit strictly required. If you do touch it (to harden), keep it under-cap and don't relax the lock-safety assertion — that's the point of the test.

In `tests/llm/test_openrouter_unit.py`, add a gate test (mirror the existing test style — `MagicMock`/`AsyncMock` for the SDK, `SimpleNamespace` for response shapes):

```python
async def test_openrouter_pre_call_gate_raises_when_exhausted(...):
    budget = Budget(cap_usd=0.0)  # already exhausted
    client = OpenRouterClient(..., budget=budget)
    with pytest.raises(BudgetExceededError):
        await client.complete(prompt="x", model="...", ...)
    # Assert no SDK call was issued (gate fires before the network).
```

- [ ] **Step 5: End-to-end pipeline test.**

Confirm `pipeline.py:331`'s `BudgetExceededError` handler still catches both reserve-failed (embedding) and settle-failed (LLM) paths. Add a test:

```python
async def test_pipeline_marks_budget_exceeded_on_llm_overspend(...):
    # FakeLLMClient that reports cost > cap on first call.
    # Assert Report.budget_exceeded == True.
    # Assert pipeline returns gracefully (truncated-run shape).
```

Use existing pipeline test scaffolding in `tests/test_pipeline_e2e.py` (note: there is no `tests/test_pipeline.py`).

- [ ] **Step 6: Verify.** `just test -k budget && just test -k pipeline && just typecheck && just lint`.

---

## Task 4 — Surface min_similarity drops on Report and in logs

**Files:**
- Modify: `slopmortem/models.py` (`PipelineMeta`)
- Modify: `slopmortem/pipeline.py`
- Modify: `slopmortem/render.py` (the existing banner does **not** include the phrase "filtered out" — Step 3 must add it explicitly so the test in Step 4 can assert on it)
- Modify: `tests/test_pipeline_e2e.py` (the actual file — there is no `tests/test_pipeline.py`)

**Steps:**

- [ ] **Step 1: Add `filtered_post_synth` field.**

In `slopmortem/models.py:264`, after `filtered_pre_synth: int = 0`, add:

```python
filtered_post_synth: int = 0
```

- [ ] **Step 2: Capture and log drops in pipeline.**

At the top of `slopmortem/pipeline.py`, add:

```python
import logging
logger = logging.getLogger(__name__)
```

(Verify `logging` import style matches the file — currently `pipeline.py` has no logger. Add at the top with other stdlib imports.)

At `pipeline.py:286`, capture pre-filter length:

```python
ranked_in = len(reranked.ranked)
survivors = _filter_by_min_similarity(reranked.ranked, config.min_similarity_score)
if len(survivors) < ranked_in:
    logger.info(
        "min_similarity dropped %d/%d candidates post-rerank (threshold=%.2f)",
        ranked_in - len(survivors),
        ranked_in,
        config.min_similarity_score,
    )
```

At `pipeline.py:317-318`, similarly capture and log:

```python
synth_in = len(successes)
successes = _filter_synth_by_min_similarity(successes, config.min_similarity_score)
filtered_post_synth = synth_in - len(successes)
if filtered_post_synth > 0:
    logger.info(
        "min_similarity dropped %d/%d syntheses post-synth (threshold=%.2f)",
        filtered_post_synth,
        synth_in,
        config.min_similarity_score,
    )
```

Pass `filtered_post_synth` into `PipelineMeta` at the Report-construction site (find with `grep -n "PipelineMeta(" slopmortem/pipeline.py`).

- [ ] **Step 3: Update render banner.**

The dispatch site is `render.py:184` (inside `render(report)`) — `if not report.candidates and not report.pipeline_meta.budget_exceeded:` calls `_render_no_comparables_banner(report.pipeline_meta.min_similarity_score)`. The helper is defined at `render.py:155` and currently takes only `threshold: float`. The existing banner reads:

```
No comparables passed similarity threshold {threshold:.1f}. The pitch may be outside the corpus, or the threshold may be too strict (min_similarity_score in slopmortem.toml).
```

It mentions `min_similarity_score` but does **not** contain "filtered out".

Change the helper signature to take both the meta and the threshold, **and update the dispatch site at line 184 in the same edit** (otherwise the call argument count won't match):

```python
def _render_no_comparables_banner(meta: PipelineMeta, threshold: float) -> str:
    dropped = meta.filtered_pre_synth + meta.filtered_post_synth
    if dropped > 0:
        return (
            f"No comparables passed similarity threshold {threshold:.1f}. "
            f"{dropped} candidate(s) were filtered out — try lowering "
            f"min_similarity_score in slopmortem.toml."
        )
    return (
        f"No comparables passed similarity threshold {threshold:.1f}. "
        f"The pitch may be outside the corpus, or the threshold may be too "
        f"strict (min_similarity_score in slopmortem.toml)."
    )
```

Update the dispatch (`render.py:184`):

```python
sections.append(_render_no_comparables_banner(report.pipeline_meta, report.pipeline_meta.min_similarity_score))
```

(`PipelineMeta` is already imported in this file via `from slopmortem.models import ...` — verify when editing.)

The "filtered out" phrasing is **load-bearing** for the Step 4 banner test — keep that exact substring.

- [ ] **Step 4: Tests.**

```python
async def test_post_synth_filter_records_drop_on_pipeline_meta(...):
    # Configure min_similarity_score so 2 of 3 synth results fail the threshold.
    report = await run_query_pipeline(...)
    assert report.pipeline_meta.filtered_post_synth == 2

async def test_render_banner_mentions_filter_drops(...):
    # Build a Report with candidates=[] and filtered_post_synth=3.
    rendered = render_report(report)
    assert "filtered out" in rendered.lower()
    assert "min_similarity" in rendered.lower()

async def test_pipeline_logs_drops_at_info(caplog, ...):
    with caplog.at_level(logging.INFO, logger="slopmortem.pipeline"):
        await run_query_pipeline(...)
    assert any("min_similarity dropped" in r.message for r in caplog.records)
```

- [ ] **Step 5: Verify.** `just test -k "pipeline or render" && just typecheck && just lint`.

---

## Task 5 — Skip `mark_complete` when chunk count is zero

**Files:**
- Modify: `slopmortem/ingest.py` (the function being modified is `_process_entry`, not `ingest_one_entry` — the latter does not exist; current return values are `"processed"` and `"skipped"`; the outcome switch lives at `ingest.py:1087-1090` and updates `IngestResult`)
- Modify: `slopmortem/tracing/events.py` (add `INGEST_ENTRY_EMPTY_CHUNKS`; existing pattern: `CONSTANT = "snake_case"`)
- Modify: tests — there is no `tests/test_ingest.py`; use `tests/test_ingest_orchestration.py` (the orchestrator-level test file) or add a sibling `tests/test_ingest_zero_chunk.py`

**Steps:**

- [ ] **Step 1: Capture chunk count and guard.**

At `slopmortem/ingest.py:783-791` (inside `_process_entry`), replace:

```python
_ = await _embed_and_upsert(
    canonical_id=canonical_id,
    body=merged,
    payload=payload,
    corpus=corpus,
    embed_client=embed_client,
    embed_model_id=config.embed_model_id,
    sparse_encoder=sparse_encoder,
)
```

with:

```python
chunks_written = await _embed_and_upsert(
    canonical_id=canonical_id,
    body=merged,
    payload=payload,
    corpus=corpus,
    embed_client=embed_client,
    embed_model_id=config.embed_model_id,
    sparse_encoder=sparse_encoder,
)
if chunks_written == 0:
    # Body produced zero chunks (whitespace-only or tokenizer-empty after slop
    # survival). Don't mark_complete: a "complete" journal row with no Qdrant
    # points is silent corpus drift. Reconcile drift class (a) catches this
    # retroactively; surface it now so the operator sees it.
    Laminar.event(
        name=SpanEvent.INGEST_ENTRY_EMPTY_CHUNKS,
        attributes={"canonical_id": canonical_id},
    )
    logger.warning(
        "ingest skipped mark_complete: zero chunks for canonical_id=%s",
        canonical_id,
    )
    return "skipped_empty"
```

Notes:
- `Laminar` is already imported at `ingest.py:52` and used elsewhere in the file (e.g. `ingest.py:878-879`). There is **no** `_emit_event` helper in ingest.py — use `Laminar.event(name=..., attributes=...)` directly. (Confirm the `attributes` kwarg name matches existing call sites when editing.)
- `logger` is already in scope at `ingest.py:85`.

- [ ] **Step 2: Add a counter to `IngestResult` and route the new outcome.**

`IngestResult` is the dataclass aggregator for `ingest()`'s outcomes (defined around `ingest.py:315`). Add a field:

```python
skipped_empty: int = 0
```

At the outcome switch (`ingest.py:1087-1090`) extend the elif chain so `"skipped_empty"` increments `result.skipped_empty` instead of silently no-op-ing:

```python
elif outcome == "skipped_empty":
    result.skipped_empty += 1
```

(Reusing the existing `"skipped"` return is **not** safe — `"skipped"` today means "alias_blocked / resolver_flipped / idempotent skip-key match", which are entirely different journal states; mixing zero-chunk drift into that bucket would hide it from CLI output.)

- [ ] **Step 3: Add the span event.**

In `slopmortem/tracing/events.py`, add to the `SpanEvent` enum (existing members live around lines 8-25):

```python
INGEST_ENTRY_EMPTY_CHUNKS = "ingest_entry_empty_chunks"
```

Match the existing snake_case convention (`INGEST_ENTRY_FAILED = "ingest_entry_failed"`), not dotted notation.

- [ ] **Step 4: Test.**

```python
async def test_zero_chunk_body_skips_mark_complete(tmp_path, ...):
    # Use InMemoryCorpus (defined in slopmortem.ingest) and a body that
    # chunk_markdown returns [] for — whitespace-only or tokenizer-empty.
    body = "   \n\t  "
    journal = MergeJournal(tmp_path / "journal.sqlite")
    corpus = InMemoryCorpus()
    outcome = await _process_entry(
        ..., body=body, journal=journal, corpus=corpus,
        canonical_id=canonical_id, source=source, source_id=source_id,
    )
    assert outcome == "skipped_empty"
    # No Qdrant point upserted (InMemoryCorpus stores them on `.points`):
    assert corpus.points == []
    # No terminal-state journal row for this (canonical_id, source, source_id):
    # MergeJournal has no `is_complete` helper — use fetch_by_key, which
    # returns a list of 0 or 1 row dicts keyed on (canonical_id, source, source_id).
    rows = await journal.fetch_by_key(canonical_id, source, source_id)
    assert all(r["merge_state"] != "complete" for r in rows)
```

(`MergeJournal.fetch_by_key(canonical_id, source, source_id) -> list[dict]` at `slopmortem/corpus/merge.py:287` returns at most one row; rows carry a `merge_state` field whose `"complete"` value is the terminal state — see `merge.py:269`. There is no `is_complete` accessor.)

- [ ] **Step 5: Verify.** `just test -k ingest && just typecheck && just lint`.

---

## Task 6 — Implement `delete_chunks_for_canonical` on QdrantCorpus + narrow exception handling

**Files:**
- Modify: `slopmortem/corpus/qdrant_store.py`
- Modify: `slopmortem/ingest.py`
- Modify: `slopmortem/cli.py` (remove the cast acknowledging the gap)
- Modify: `tests/corpus/test_qdrant_store.py` (or create — verify location)

**Steps:**

- [ ] **Step 1: Implement `delete_chunks_for_canonical` on `QdrantCorpus`.**

In `slopmortem/corpus/qdrant_store.py`, add a method to the `QdrantCorpus` class:

**Return type must be `-> None`** to match the Protocol (`ingest.py:126`) and the existing `InMemoryCorpus` impl (`ingest.py:236`). Don't return an int — the audit's chunk-count idea conflicts with the existing contract.

**`QdrantCorpus` uses `AsyncQdrantClient`** (`qdrant_store.py:75`+) — calls are awaited directly (e.g. `await self._client.upsert(...)` at line 338). Do **not** wrap in `asyncio.to_thread` or `anyio.to_thread.run_sync`; that's the wrong threading model for this client.

```python
async def delete_chunks_for_canonical(self, canonical_id: str) -> None:
    """Delete all chunk points whose payload.canonical_id matches.

    Idempotent: deleting when no points exist does not raise. Raises on
    transport/auth failures; the caller (ingest._process_entry) decides
    whether to abort the entry or proceed.
    """
    from qdrant_client.http.models import (
        FieldCondition,
        Filter,
        FilterSelector,
        MatchValue,
    )

    selector = FilterSelector(
        filter=Filter(
            must=[
                FieldCondition(
                    key="canonical_id",
                    match=MatchValue(value=canonical_id),
                )
            ]
        )
    )
    await self._client.delete(
        collection_name=self._collection,
        points_selector=selector,
    )
```

Mirror the inline-import style used by the existing `query()` method (`qdrant_store.py:153+`) — keeps top-level imports lean. Verify the exact import path for `FilterSelector` against the installed `qdrant-client` 1.17.1 (validators confirmed it lives under `qdrant_client.http.models`).

- [ ] **Step 2: Replace `contextlib.suppress(Exception)` with narrow handling.**

At `slopmortem/ingest.py:769-771`, replace:

```python
if existing:
    with contextlib.suppress(Exception):
        await corpus.delete_chunks_for_canonical(canonical_id)
```

with:

```python
if existing:
    try:
        await corpus.delete_chunks_for_canonical(canonical_id)
    except Exception as exc:
        # Failed delete on re-merge: re-upserting on top of orphans would
        # leak higher-index chunks from a longer prior body. Abort entry,
        # let reconcile catch the drift on a later pass.
        Laminar.event(
            name=SpanEvent.INGEST_ENTRY_FAILED,
            attributes={
                "canonical_id": canonical_id,
                "stage": "delete_chunks",
                "error": str(exc),
            },
        )
        logger.warning(
            "ingest aborted entry: delete_chunks_for_canonical failed for %s: %s",
            canonical_id, exc,
        )
        return "failed"
```

Notes:
- `SpanEvent.INGEST_ENTRY_FAILED` already exists in `tracing/events.py` (around line 24) — no enum addition needed.
- Use `Laminar.event(name=..., attributes=...)` (matches the call style at `ingest.py:878-879`); there is no `_emit_event` helper in `ingest.py`.
- Add `"failed"` to the outcome switch at `ingest.py:1087-1090` and to `IngestResult` (alongside `skipped_empty` from Task 5) so the count surfaces in the CLI summary.
- If `contextlib` is no longer referenced in `ingest.py` after this change, remove the import (validators confirmed the only use today is line 770 — safe to drop).
- Catching bare `Exception` is intentional here: qdrant-client raises a wide range of transport/auth/validation errors, and the recovery action is the same for all of them. If a tighter base class is ever exported, narrow it then.

- [ ] **Step 3: Remove the cast in CLI.**

At `slopmortem/cli.py:707-711`, the file currently casts to `"IngestCorpus"` because `QdrantCorpus` lacked `delete_chunks_for_canonical`. Remove the cast (line 711); leave the surrounding instantiation alone. **Also remove the sibling cast at `slopmortem/evals/corpus_recorder.py:183`** (comment block at lines 173–175 explains the gap; the `cast("IngestCorpus", qcorpus)` itself is at line 183). Both casts are obsoleted by Step 1.

- [ ] **Step 4: Tests.**

`QdrantCorpus` does **not** expose a `get_chunks(canonical_id)` accessor; the validator-suggested test API doesn't exist. Use `qdrant_client.scroll()` with the same filter to verify deletion. The fixture `qdrant_corpus` also doesn't exist yet — `tests/corpus/conftest.py:28-38` provides `qdrant_client` (an `AsyncQdrantClient`); build the `QdrantCorpus` from it inline or add a fixture in the same conftest.

In `tests/corpus/test_qdrant_store.py` (file does not exist; create it):

```python
@pytest.mark.requires_qdrant
async def test_delete_chunks_for_canonical_removes_matching_points(qdrant_corpus, qdrant_client):
    canonical_id = "test:abc123"
    other = "test:other"
    for idx in range(3):
        await qdrant_corpus.upsert_chunk(_make_chunk(canonical_id, idx))
    await qdrant_corpus.upsert_chunk(_make_chunk(other, 0))

    await qdrant_corpus.delete_chunks_for_canonical(canonical_id)

    # Verify via scroll + filter — there is no `get_chunks` helper.
    from qdrant_client.http.models import FieldCondition, Filter, MatchValue
    matched, _ = await qdrant_client.scroll(
        collection_name=qdrant_corpus._collection,
        scroll_filter=Filter(must=[FieldCondition(
            key="canonical_id", match=MatchValue(value=canonical_id))]),
        limit=10,
    )
    assert matched == []
    other_matched, _ = await qdrant_client.scroll(
        collection_name=qdrant_corpus._collection,
        scroll_filter=Filter(must=[FieldCondition(
            key="canonical_id", match=MatchValue(value=other))]),
        limit=10,
    )
    assert len(other_matched) == 1

@pytest.mark.requires_qdrant
async def test_delete_chunks_idempotent_when_no_points(qdrant_corpus):
    await qdrant_corpus.delete_chunks_for_canonical("nonexistent:id")  # must not raise
```

In an ingest test (use `tests/test_ingest_orchestration.py` or create `tests/test_ingest_delete_failure.py`):

```python
async def test_delete_failure_aborts_entry_does_not_mark_complete(...):
    # corpus stub whose delete_chunks_for_canonical raises; simulate existing
    # canonical_id (the existing=True branch in _process_entry).
    outcome = await _process_entry(
        ..., corpus=failing_corpus,
        canonical_id=canonical_id, source=source, source_id=source_id,
    )
    assert outcome == "failed"
    rows = await journal.fetch_by_key(canonical_id, source, source_id)
    assert all(r["merge_state"] != "complete" for r in rows)
```

(Same `fetch_by_key` shape as Task 5 Step 4 — `MergeJournal` has no `is_complete` helper.)

- [ ] **Step 5: Verify.** `just test -k "qdrant or ingest" && just typecheck && just lint`. The `requires_qdrant` tests need `localhost:16333` (per `Config.qdrant_port`, not 6333); skip locally if Qdrant isn't running — CI runs it via the service container.

---

## Task 7 — Use `relativedelta` for cutoff_iso

The function is `cutoff_iso` (public, no leading underscore — `pipeline.py:112`). The plan previously referred to `_cutoff_iso`; that name does not exist.

**Files:**
- Modify: `pyproject.toml` (add `python-dateutil` as direct dep)
- Modify: `slopmortem/pipeline.py`
- Modify: `tests/test_pipeline_e2e.py` (the actual file — there is no `tests/test_pipeline.py`)

**Steps:**

- [ ] **Step 1: Add direct dep.**

In `pyproject.toml:5-24`, add to the `dependencies` list:

```toml
"python-dateutil>=2.9",
```

It's already in `uv.lock` as a transitive — adding a direct constraint makes the use explicit. Run `uv sync` after.

- [ ] **Step 2: Replace `timedelta(days=365 * years)` with `relativedelta(years=N)`.**

In `slopmortem/pipeline.py`:

- Remove `_DAYS_PER_YEAR = 365` at line 46.
- At the top of the file, add: `from dateutil.relativedelta import relativedelta`.
- At line 120, change:

```python
return (datetime.now(UTC) - timedelta(days=_DAYS_PER_YEAR * years_filter)).date().isoformat()
```

to:

```python
return (datetime.now(UTC) - relativedelta(years=years_filter)).date().isoformat()
```

If `timedelta` is no longer used in this file after the change, remove the import.

- [ ] **Step 3: Test.**

```python
from slopmortem.pipeline import cutoff_iso

def test_cutoff_iso_handles_leap_year_correctly(monkeypatch):
    """relativedelta(years=4) from 2024-02-29 must land on 2020-02-29, not 2020-02-28."""
    fixed_now = datetime(2024, 2, 29, 12, 0, 0, tzinfo=UTC)

    class _Now:
        @staticmethod
        def now(tz):
            return fixed_now
    monkeypatch.setattr("slopmortem.pipeline.datetime", _Now)
    assert cutoff_iso(years_filter=4) == "2020-02-29"

def test_cutoff_iso_none_passthrough():
    assert cutoff_iso(years_filter=None) is None
```

`pipeline.py` imports `datetime` as a name (`from datetime import UTC, datetime, timedelta` at line 20), so `monkeypatch.setattr("slopmortem.pipeline.datetime", ...)` rebinds the right symbol.

- [ ] **Step 4: Verify.** `just test -k cutoff && just typecheck && just lint`.

---

## Verification — full sweep after all 7 tasks

- [ ] **Final sweep.** Run the full suite: `just lint && just typecheck && just test && just coverage`.
- [ ] **Cassette check.** None of the new tests use cassettes — they all use fakes/`InMemoryCorpus`/mocks per project convention. Existing cassettes are unaffected: adding a defaulted `injection_detected` to `Synthesis` doesn't change LLM request/response shape (which is what cassettes capture). `just eval` should pass unchanged. Skip `just eval-record`.
- [ ] **Docs final pass.** Re-read CLAUDE.md's load-bearing section and confirm: (1) the injection contract now matches runtime (Task 1), (2) the precedence claim (Task 2) matches code, (3) nothing else has drifted.

## Risks and notes

- **Task 1 cassette refresh.** Validators searched `tests/fixtures/cassettes/` and found no serialized `Synthesis` objects (only `.gitkeep`). Adding a defaulted field is safe. If an eval cassette later proves to pickle a full `Report`, re-record with `just eval-record` (~$2; confirm with user first).
- **Task 2 docs drift.** Docs and code disagreed; the code is the truth. After this task, lock the precedence with the three new tests so future docs drift gets caught by CI rather than reading.
- **Task 3 existing test sanity.** `test_reserve_settle_under_gather` in `tests/test_budget.py` uses `cap_usd=1.00` with three concurrent `settle(0.20)` calls (total 0.60). It stays under-cap and remains green under the new contract — no edit required. Re-verify after the change; if it ever flips to over-cap settling, harden it then.
- **Task 3 fan-out tail overshoot.** Documented limitation, not a hard cap. If a hard cap is required later, switch to estimator-based reserve (approach D in the brainstorm).
- **Task 5 + Task 6 outcome routing.** Both tasks add new return values from `_process_entry` (`"skipped_empty"`, `"failed"`). Each must extend `IngestResult` and the outcome switch at `ingest.py:1087-1090` together — a missing branch would silently drop the count from the CLI summary.
- **Task 6 Qdrant API.** `qdrant-client` 1.17.1 — validators confirmed `Filter`, `FieldCondition`, `MatchValue`, `FilterSelector` all live under `qdrant_client.http.models`. `QdrantCorpus` uses `AsyncQdrantClient`; await the client directly instead of threading.
- **Task 6 cast removal.** The cast at `cli.py:707-711` is the primary target. Validators also flagged a sibling at `slopmortem/evals/corpus_recorder.py:183` (comment lines 173–175) and the optional-method probe in `slopmortem/corpus/reconcile.py` (around lines 300, 343, 346 — `getattr` based, already safe but worth re-reading once the method exists). Search `grep -rn "cast.*delete_chunks\|cast.*QdrantCorpus\|cast.*IngestCorpus" slopmortem/` once more before declaring the task done.
- **No backwards-compat shims.** Per CLAUDE.md: don't leave `# removed` comments or rename-as-deprecated stubs. Delete completely where possible.
