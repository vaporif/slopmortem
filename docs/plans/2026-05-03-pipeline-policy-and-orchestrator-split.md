# Pipeline Policy + Orchestrator Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or `/team-feature` to implement this plan task-by-task, per the Execution Strategy below. Steps use checkbox (`- [ ]`) syntax — these are **persistent durable state**, not visual decoration. The executor edits the plan file in place: `- [ ]` → `- [x]` the instant a step verifies, before moving on. On resume (new session, crash, takeover), the executor scans existing `- [x]` marks and skips them — these steps are NOT redone. TodoWrite mirrors this state in-session; the plan file is the source of truth across sessions.

**Subagent rule (standing user feedback):** Subagents must NOT run `git add`, `git commit`, or any other staging/commit command. The parent owns commit authorship. Briefs MUST forbid commits explicitly.

**Scope discipline (standing user feedback):** Each task lists exact CREATE / MODIFY / DELETE files. Do NOT touch anything outside that list. No "while I'm here" cleanups, no extra tests, no new dependencies, no helper extractions beyond what the task names. If a step looks too tight, ask — don't widen.

**Goal:** Pull similarity-policy decisions out of `slopmortem/pipeline.py` into the stage modules that own those decisions (`stages/llm_rerank.py`, `stages/synthesize.py`), and split the 379-line junk drawer `slopmortem/ingest/_orchestrator.py` into three focused modules organised by concept layer (ports / impls / helpers).

**Architecture:** Two unrelated reorganisations bundled into one plan. (1) `pipeline.py` becomes pure orchestration; the post-rerank top-N selection and the post-synth re-filter live with the stages they follow; a small `mean()` method on `SimilarityScores` removes the only shared math primitive. (2) `_orchestrator.py` is deleted; `_ports.py` (protocols, type alias, `IngestPhase`, `INGEST_PHASE_LABELS`, `IngestProgress`, `NullProgress`, `IngestResult`, `_Point`, `Corpus`, `SlopClassifier`), `_helpers.py` (pure helpers + `_RELIABILITY_RANK`), and `_impls.py` (`InMemoryCorpus`, `FakeSlopClassifier`, `HaikuSlopClassifier`) take its place.

**Tech Stack:** Python 3.13, Pydantic v2, basedpyright (strict), anyio, ruff, just, pytest with xdist + asyncio_mode=auto.

## Execution Strategy

Subagents. Sequential dependency chain — both refactors are file moves guarded by the existing test suite, so there is no independent work to fan out, and the user has standing feedback preferring one-task-at-a-time execution. Each task ends with `just lint && just typecheck && just test` green before the next begins.

## Task Dependency Graph

- Task 1 [AFK]: depends on `none` → batch 1
- Task 2 [AFK]: depends on `Task 1` → batch 2
- Task 3 [AFK]: depends on `Task 2` → batch 3
- Task 4 [AFK]: depends on `Task 3` → batch 4
- Task 5 [AFK]: depends on `Task 4` → batch 5
- Task 6 [AFK]: depends on `Task 5` → batch 6
- Task 7 [AFK]: depends on `Task 6` → batch 7
- Task 8 [AFK]: depends on `Task 7` → batch 8
- Task 9 [AFK]: depends on `Task 8` → batch 9

## Agent Assignments

- Task 1: Add `SimilarityScores.mean()` → python-development:python-pro
- Task 2: Move post-rerank selection into `stages/llm_rerank.py` → python-development:python-pro
- Task 3: Move post-synth filter into `stages/synthesize.py` → python-development:python-pro
- Task 4: Switch `pipeline.py` to call new stage policy + delete old helpers → python-development:python-pro
- Task 5: Create `ingest/_ports.py` and migrate symbols out of `_orchestrator.py` → python-development:python-pro
- Task 6: Create `ingest/_helpers.py` and migrate helpers out of `_orchestrator.py` → python-development:python-pro
- Task 7: Create `ingest/_impls.py` and migrate classifier/corpus impls out of `_orchestrator.py` → python-development:python-pro
- Task 8: Delete `_orchestrator.py`, update `.importlinter`, rename test file → python-development:python-pro
- Task 9: Run post-implementation polish → general-purpose

---

## Design choices

### Where does the "mean of four perspective scores" live?

`_mean_similarity_score` is used by both the post-rerank filter (against `ScoredCandidate.perspective_scores`) and the post-synth filter (against `Synthesis.similarity`). Both targets are `SimilarityScores`. Three options:

- **Method on `SimilarityScores` (chosen).** Pro: same class, same math; readable call sites (`s.perspective_scores.mean()`); no new module. Con: adds a method to a Pydantic shape that's currently field-only — small precedent shift.
- Free function in a shared `slopmortem/stages/_scoring.py`. Pro: keeps `SimilarityScores` field-only. Con: extra module for a 4-line function; both stages import from a sibling private module.
- Duplicate the function in each stage file. Pro: zero coupling. Con: literal copy-paste, fails the "three similar lines is better than a premature abstraction" sniff in the other direction (this is one shared computation, not a coincidence).

**Auto-selected.** The model already owns the field shape; `mean()` is the canonical reduction over those fields. The "field-only Pydantic" convention is a soft preference, not a project rule.

### How granular should the orchestrator split be?

The user's framing was "ports, fakes, helpers." Two readings:

- **Three modules: `_ports.py` / `_impls.py` / `_helpers.py` (chosen).** Pro: matches the user's three-layer mental model; `_impls.py` holds every runtime impl of a port (`InMemoryCorpus`, `FakeSlopClassifier`, `HaikuSlopClassifier`) regardless of fake-vs-prod, which is honest about what they share (impl of the same port surface). Con: mixes a test fake (`InMemoryCorpus`) with a prod runtime (`HaikuSlopClassifier`) in one file.
- Four modules: `_ports.py` / `_fakes.py` / `_haiku_slop.py` / `_helpers.py`. Pro: separates test fakes from prod runtime. Con: one-class file (`_haiku_slop.py`) is a small-file fetish; the user explicitly named three layers.

**Chosen: three modules.** Honour the user's stated split. `_impls.py` is small enough that the fake-vs-prod mix doesn't get in the way; if it grows later, splitting it is a 10-minute follow-up.

### Re-export shim during the split, or atomic migration?

- **Atomic migration (chosen).** Each task creates the new module, moves the symbols out of `_orchestrator.py`, and updates every import in one task. Verified via `just lint && just typecheck && just test`.
- Shim-then-delete. Add a re-export in `_orchestrator.py` so external imports keep working until a final cleanup task. Pro: each task touches fewer files. Con: dead re-export code lands and is undone in the next commit; doubles the diff churn; introduces an interim state where the same symbol is reachable via two paths.

**Chosen: atomic migration.** The blast radius is contained — every importer of `_orchestrator.py` is a sibling module in `slopmortem/ingest/`, the package facade `slopmortem/ingest/__init__.py`, or one test file. All edits fit in one task.

---

## Task 1: Add `SimilarityScores.mean()`

**Files:**
- Modify: `slopmortem/models.py`
- Test: `tests/test_models.py`

**Brief for the implementer:** Add a single `mean()` method to `SimilarityScores`. No other model changes. No new imports. No callers updated yet — this is a pure model addition.

**Anti-scope:** Do NOT add other methods, properties, validators, or `__repr__` changes. Do NOT touch any other model class. Do NOT add a method to `PerspectiveScore`. Do NOT commit.

- [x] **Step 1: Read the current `SimilarityScores` definition**

Read `slopmortem/models.py:76-82`. Confirm it's exactly:

```python
class SimilarityScores(BaseModel):
    """Closed set of similarity perspectives the reranker scores against."""

    business_model: PerspectiveScore
    market: PerspectiveScore
    gtm: PerspectiveScore
    stage_scale: PerspectiveScore
```

- [x] **Step 2: Write the failing test for `mean()`**

Append to `tests/test_models.py`:

```python
def test_similarity_scores_mean_averages_four_perspectives() -> None:
    from slopmortem.models import PerspectiveScore, SimilarityScores  # noqa: PLC0415

    scores = SimilarityScores(
        business_model=PerspectiveScore(score=8.0, rationale="x"),
        market=PerspectiveScore(score=6.0, rationale="x"),
        gtm=PerspectiveScore(score=4.0, rationale="x"),
        stage_scale=PerspectiveScore(score=2.0, rationale="x"),
    )
    assert scores.mean() == 5.0
```

- [x] **Step 3: Run the failing test**

Run: `uv run pytest tests/test_models.py::test_similarity_scores_mean_averages_four_perspectives -v`
Expected: FAIL with `AttributeError: 'SimilarityScores' object has no attribute 'mean'`.

- [x] **Step 4: Add the `mean()` method**

Edit `slopmortem/models.py`, replacing the body of `SimilarityScores`:

```python
class SimilarityScores(BaseModel):
    """Closed set of similarity perspectives the reranker scores against."""

    business_model: PerspectiveScore
    market: PerspectiveScore
    gtm: PerspectiveScore
    stage_scale: PerspectiveScore

    def mean(self) -> float:
        """Mean of the four perspective scores. Used by post-rerank and post-synth filters."""
        return (
            self.business_model.score
            + self.market.score
            + self.gtm.score
            + self.stage_scale.score
        ) / 4
```

- [x] **Step 5: Verify the test now passes**

Run: `uv run pytest tests/test_models.py::test_similarity_scores_mean_averages_four_perspectives -v`
Expected: PASS.

- [x] **Step 6: Verify the full suite still passes**

Run: `just lint && just typecheck && just test`
Expected: all green. The new method is unused so far, so no behaviour drift.

---

## Task 2: Move post-rerank top-N selection into `stages/llm_rerank.py`

**Files:**
- Modify: `slopmortem/stages/llm_rerank.py`
- Modify: `tests/stages/test_llm_rerank.py`
- Modify: `tests/test_pipeline_e2e.py` (delete the moved tests only)

**Brief for the implementer:** Add three things to `stages/llm_rerank.py`: a module-level `logger`, a private `_join_by_id` helper, and a public `select_top_n_by_similarity` function. Move the unit tests for `_filter_by_min_similarity` and `_join_to_candidates` out of `tests/test_pipeline_e2e.py` into `tests/stages/test_llm_rerank.py`, rewriting them to call the new public function. `pipeline.py` is NOT modified in this task — it still calls its own `_select_top_n` etc.

**Anti-scope:** Do NOT modify `pipeline.py` or `stages/__init__.py` yet. Do NOT touch the existing `llm_rerank()` function body. Do NOT delete the helpers in `pipeline.py` yet. Do NOT add tracing decorators to the new function — it's pure. Do NOT commit.

- [x] **Step 1: Add `logger` + new helpers to `stages/llm_rerank.py`**

At the top of `slopmortem/stages/llm_rerank.py`, add `import logging` and a module logger after the other imports. Append the new helpers below the existing `llm_rerank()` function.

The full file should add these four pieces:

```python
# add to imports section, after `from typing import TYPE_CHECKING`:
import logging
```

```python
# add after the existing imports, before the @observe decorator:
logger = logging.getLogger(__name__)
```

```python
# update the TYPE_CHECKING block: add Candidate (already present) and ScoredCandidate
if TYPE_CHECKING:
    from slopmortem.config import Config
    from slopmortem.llm import LLMClient
    from slopmortem.models import Candidate, Facets, ScoredCandidate
```

```python
# add at the end of the file:
def _join_by_id(
    retrieved: list[Candidate], ranked: list[ScoredCandidate]
) -> list[Candidate]:
    # Drops any ranked id missing from retrieved — defensive, since the
    # reranker only ever sees retrieved ids.
    id_to_candidate = {c.canonical_id: c for c in retrieved}
    out: list[Candidate] = []
    for s in ranked:
        cand = id_to_candidate.get(s.candidate_id)
        if cand is not None:
            out.append(cand)
    return out


def select_top_n_by_similarity(
    *,
    retrieved: list[Candidate],
    ranked: list[ScoredCandidate],
    min_similarity: float,
    n_synthesize: int,
) -> tuple[list[Candidate], int]:
    """Apply the post-rerank min-similarity filter and slice to *n_synthesize*.

    Returns ``(top_n, dropped_count)`` where ``dropped_count`` is
    ``n_synthesize - len(top_n)`` — conflates min-sim drops with retrieve
    under-fill, which is what ``PipelineMeta.filtered_pre_synth`` records.
    """
    survivors = [s for s in ranked if s.perspective_scores.mean() >= min_similarity]
    sim_dropped = len(ranked) - len(survivors)
    if sim_dropped > 0:
        logger.info(
            "min_similarity dropped %d/%d candidates post-rerank (threshold=%.2f)",
            sim_dropped,
            len(ranked),
            min_similarity,
        )
    top_n = _join_by_id(retrieved, survivors)[:n_synthesize]
    return top_n, max(0, n_synthesize - len(top_n))
```

- [x] **Step 2: Read `tests/test_pipeline_e2e.py:694-785` to capture the existing helper tests**

The relevant tests are:
- `test_join_to_candidates_preserves_rerank_order` (`tests/test_pipeline_e2e.py:694-718`)
- `test_join_to_candidates_drops_unknown_ids` (`tests/test_pipeline_e2e.py:721-741`)
- `_scored_with` helper (`tests/test_pipeline_e2e.py:744-754`)
- `test_filter_by_min_similarity_drops_below_threshold` (`tests/test_pipeline_e2e.py:757-765`)
- `test_filter_by_min_similarity_preserves_order` (`tests/test_pipeline_e2e.py:768-776`)
- `test_filter_by_min_similarity_empty_when_all_below` (`tests/test_pipeline_e2e.py:779-785`)

These will move to `tests/stages/test_llm_rerank.py` and be rewritten against `select_top_n_by_similarity` / `_join_by_id`.

- [x] **Step 3: Append migrated tests to `tests/stages/test_llm_rerank.py`**

Append to `tests/stages/test_llm_rerank.py`:

```python
def _scored_with(
    cid: str, *, bm: float, mk: float, gtm: float, ss: float
):  # noqa: ANN202 - test helper
    from slopmortem.models import (  # noqa: PLC0415
        PerspectiveScore,
        ScoredCandidate,
        SimilarityScores,
    )

    return ScoredCandidate(
        candidate_id=cid,
        perspective_scores=SimilarityScores(
            business_model=PerspectiveScore(score=bm, rationale="x"),
            market=PerspectiveScore(score=mk, rationale="x"),
            gtm=PerspectiveScore(score=gtm, rationale="x"),
            stage_scale=PerspectiveScore(score=ss, rationale="x"),
        ),
        rationale="r",
    )


def _retrieved_candidate(canonical_id: str) -> Candidate:
    return Candidate(
        canonical_id=canonical_id,
        score=0.9,
        payload=CandidatePayload(
            name=canonical_id,
            summary=f"{canonical_id} summary",
            body=f"{canonical_id} body",
            facets=_facets(),
            founding_date=date(2018, 1, 1),
            failure_date=date(2023, 1, 1),
            founding_date_unknown=False,
            failure_date_unknown=False,
            provenance="curated_real",
            slop_score=0.0,
            sources=[],
            text_id=canonical_id.replace("-", "") + "0123456789",
        ),
    )


def test_join_by_id_preserves_rerank_order() -> None:
    from slopmortem.stages.llm_rerank import _join_by_id  # noqa: PLC0415

    retrieved = [_retrieved_candidate(f"cand-{i}") for i in range(5)]
    ranked = [
        _scored_with("cand-3", bm=1.0, mk=1.0, gtm=1.0, ss=1.0),
        _scored_with("cand-0", bm=1.0, mk=1.0, gtm=1.0, ss=1.0),
        _scored_with("cand-2", bm=1.0, mk=1.0, gtm=1.0, ss=1.0),
    ]
    joined = _join_by_id(retrieved, ranked)
    assert [c.canonical_id for c in joined] == ["cand-3", "cand-0", "cand-2"]


def test_join_by_id_drops_unknown_ids() -> None:
    from slopmortem.stages.llm_rerank import _join_by_id  # noqa: PLC0415

    retrieved = [_retrieved_candidate("cand-0"), _retrieved_candidate("cand-1")]
    ranked = [_scored_with("ghost", bm=1.0, mk=1.0, gtm=1.0, ss=1.0)]
    assert _join_by_id(retrieved, ranked) == []


def test_select_top_n_by_similarity_drops_below_threshold() -> None:
    from slopmortem.stages.llm_rerank import select_top_n_by_similarity  # noqa: PLC0415

    retrieved = [_retrieved_candidate("strong"), _retrieved_candidate("weak")]
    ranked = [
        _scored_with("strong", bm=7.0, mk=6.0, gtm=5.0, ss=4.0),  # mean = 5.5
        _scored_with("weak", bm=2.0, mk=2.0, gtm=2.0, ss=2.0),  # mean = 2.0
    ]
    top_n, dropped = select_top_n_by_similarity(
        retrieved=retrieved, ranked=ranked, min_similarity=4.0, n_synthesize=2
    )
    assert [c.canonical_id for c in top_n] == ["strong"]
    assert dropped == 1  # n_synthesize - len(top_n) == 2 - 1


def test_select_top_n_by_similarity_preserves_rerank_order() -> None:
    from slopmortem.stages.llm_rerank import select_top_n_by_similarity  # noqa: PLC0415

    retrieved = [_retrieved_candidate(cid) for cid in ("a", "b", "c")]
    ranked = [
        _scored_with("c", bm=5.0, mk=5.0, gtm=5.0, ss=5.0),
        _scored_with("a", bm=8.0, mk=8.0, gtm=8.0, ss=8.0),
        _scored_with("b", bm=6.0, mk=6.0, gtm=6.0, ss=6.0),
    ]
    top_n, _ = select_top_n_by_similarity(
        retrieved=retrieved, ranked=ranked, min_similarity=4.0, n_synthesize=3
    )
    assert [c.canonical_id for c in top_n] == ["c", "a", "b"]


def test_select_top_n_by_similarity_empty_when_all_below() -> None:
    from slopmortem.stages.llm_rerank import select_top_n_by_similarity  # noqa: PLC0415

    retrieved = [_retrieved_candidate("c1"), _retrieved_candidate("c2")]
    ranked = [
        _scored_with("c1", bm=2.0, mk=2.0, gtm=2.0, ss=4.0),  # mean = 2.5
        _scored_with("c2", bm=1.0, mk=1.0, gtm=1.0, ss=2.0),  # mean = 1.25
    ]
    top_n, dropped = select_top_n_by_similarity(
        retrieved=retrieved, ranked=ranked, min_similarity=4.0, n_synthesize=2
    )
    assert top_n == []
    assert dropped == 2
```

The new tests need `Candidate` and `CandidatePayload` already imported in `tests/stages/test_llm_rerank.py:14` — check that import covers `CandidatePayload` and add it if not.

- [x] **Step 4: Delete the migrated tests from `tests/test_pipeline_e2e.py`**

Edit `tests/test_pipeline_e2e.py`:
- Remove the `_filter_by_min_similarity, _filter_synth_by_min_similarity, _join_to_candidates` names from the `from slopmortem.pipeline import (...)` block at lines 38-45 — keep `QueryPhase`, `cutoff_iso`, `run_query` (and the still-needed `_filter_synth_by_min_similarity` until Task 3, see note below).
- Delete: `test_join_to_candidates_preserves_rerank_order`, `test_join_to_candidates_drops_unknown_ids`, `_scored_with`, `test_filter_by_min_similarity_drops_below_threshold`, `test_filter_by_min_similarity_preserves_order`, `test_filter_by_min_similarity_empty_when_all_below`.

**Note on `_filter_synth_by_min_similarity`:** keep that import and its one test (`test_filter_synth_by_min_similarity_drops_below_threshold` at `tests/test_pipeline_e2e.py:843-850`) untouched — Task 3 owns it. Likewise leave `_join_to_candidates` import only if a downstream test still uses it; grep before deleting. As of this plan, the only downstream user is `test_join_to_candidates_*` (deleted here), so both `_filter_by_min_similarity` and `_join_to_candidates` come out of the pipeline import block.

After the edit, the import block should read:

```python
from slopmortem.pipeline import (
    QueryPhase,
    _filter_synth_by_min_similarity,
    cutoff_iso,
    run_query,
)
```

- [x] **Step 5: Run the full suite**

Run: `just lint && just typecheck && just test`
Expected: all green. New tests pass; pipeline.py still works because it still calls its own `_select_top_n` (unchanged in this task).

---

## Task 3: Move post-synth filter into `stages/synthesize.py`

**Files:**
- Modify: `slopmortem/stages/synthesize.py`
- Modify: `tests/stages/test_synthesize.py`
- Modify: `tests/test_pipeline_e2e.py` (delete the one moved test only)

**Brief for the implementer:** Add a module-level `logger` and a public `drop_below_min_similarity` function to `stages/synthesize.py`. Move the unit test for `_filter_synth_by_min_similarity` from `tests/test_pipeline_e2e.py` to `tests/stages/test_synthesize.py`, rewriting it to call the new function. `pipeline.py` is still untouched.

**Anti-scope:** Do NOT modify `pipeline.py` or `stages/__init__.py` yet. Do NOT touch the existing `synthesize()` / `synthesize_all()`. Do NOT add tracing decorators. Do NOT commit.

- [x] **Step 1: Add `logger` + new helper to `stages/synthesize.py`**

Edit `slopmortem/stages/synthesize.py`:

```python
# add to imports section, after `from typing import TYPE_CHECKING, Any`:
import logging
```

```python
# add after the existing imports, before the synthesize_prompt_kwargs definition:
logger = logging.getLogger(__name__)
```

Append at the end of the file:

```python
def drop_below_min_similarity(
    syntheses: list[Synthesis], *, min_similarity: float
) -> tuple[list[Synthesis], int]:
    """Drop syntheses whose own similarity mean falls below *min_similarity*.

    Synthesis sometimes re-scores a candidate lower than rerank did, so a
    row that cleared the rerank-side filter can come back below the bar.
    Returns ``(kept, dropped_count)`` so the caller can record
    ``PipelineMeta.filtered_post_synth`` without recomputing.
    """
    kept = [s for s in syntheses if s.similarity.mean() >= min_similarity]
    dropped = len(syntheses) - len(kept)
    if dropped > 0:
        logger.info(
            "min_similarity dropped %d/%d candidates post-synth (threshold=%.2f)",
            dropped,
            len(syntheses),
            min_similarity,
        )
    return kept, dropped
```

- [x] **Step 2: Append migrated test to `tests/stages/test_synthesize.py`**

Append to `tests/stages/test_synthesize.py`:

```python
def _synth_with(
    cid: str, *, bm: float, mk: float, gtm: float, ss: float
):  # noqa: ANN202 - test helper
    from slopmortem.models import (  # noqa: PLC0415
        PerspectiveScore,
        SimilarityScores,
        Synthesis,
    )

    return Synthesis(
        candidate_id=cid,
        name=cid,
        one_liner="x",
        failure_date=None,
        lifespan_months=None,
        similarity=SimilarityScores(
            business_model=PerspectiveScore(score=bm, rationale="x"),
            market=PerspectiveScore(score=mk, rationale="x"),
            gtm=PerspectiveScore(score=gtm, rationale="x"),
            stage_scale=PerspectiveScore(score=ss, rationale="x"),
        ),
        why_similar="x",
        where_diverged="x",
        failure_causes=["x"],
        lessons_for_input=["x"],
        sources=[],
    )


def test_drop_below_min_similarity_drops_below_threshold() -> None:
    from slopmortem.stages.synthesize import drop_below_min_similarity  # noqa: PLC0415

    syntheses = [
        _synth_with("strong", bm=7.0, mk=6.0, gtm=5.0, ss=4.0),  # mean = 5.5
        _synth_with("synth_disagreed", bm=2.0, mk=2.0, gtm=1.0, ss=3.0),  # mean = 2.0
    ]
    kept, dropped = drop_below_min_similarity(syntheses, min_similarity=4.0)
    assert [s.candidate_id for s in kept] == ["strong"]
    assert dropped == 1


def test_drop_below_min_similarity_zero_dropped_when_all_pass() -> None:
    from slopmortem.stages.synthesize import drop_below_min_similarity  # noqa: PLC0415

    syntheses = [_synth_with("strong", bm=7.0, mk=6.0, gtm=5.0, ss=4.0)]
    kept, dropped = drop_below_min_similarity(syntheses, min_similarity=4.0)
    assert kept == syntheses
    assert dropped == 0
```

- [x] **Step 3: Delete the migrated test from `tests/test_pipeline_e2e.py`**

Edit `tests/test_pipeline_e2e.py`:
- Remove `_filter_synth_by_min_similarity` from the `from slopmortem.pipeline import (...)` block.
- Delete the helper `_synth_with` (`tests/test_pipeline_e2e.py:822-840`) and the test `test_filter_synth_by_min_similarity_drops_below_threshold` (`tests/test_pipeline_e2e.py:843-850`) — but ONLY if they aren't used elsewhere in the file. Run `grep -n "_synth_with" tests/test_pipeline_e2e.py` first; if anything else references it, leave the helper in place and delete only the moved test.

After the edit, the import block should read:

```python
from slopmortem.pipeline import (
    QueryPhase,
    cutoff_iso,
    run_query,
)
```

- [x] **Step 4: Run the full suite**

Run: `just lint && just typecheck && just test`
Expected: all green. `pipeline.py` still calls its own `_filter_synth_by_min_similarity` (unchanged in this task).

---

## Task 4: Switch `pipeline.py` to call new stage policy + delete old helpers

**Files:**
- Modify: `slopmortem/pipeline.py`
- Modify: `slopmortem/stages/__init__.py`

**Brief for the implementer:** This is the swap. Export the two new functions from `stages/__init__.py`. Update `pipeline.py` to call them. Delete `_mean_similarity_score`, `_filter_by_min_similarity`, `_filter_synth_by_min_similarity`, `_log_min_similarity_drop`, `_select_top_n`, `_join_to_candidates` from `pipeline.py`. Keep `cutoff_iso` and `_current_trace_id` exactly where they are — they are orchestration glue, not policy.

**Anti-scope:** Do NOT touch `_current_trace_id` or `cutoff_iso`. Do NOT touch the `QueryProgress` / `NullQueryProgress` / `QueryPhase` definitions. Do NOT change `Report` / `PipelineMeta` field names. Do NOT modify any stage internals beyond exports. Do NOT commit.

- [x] **Step 1: Export the new stage functions from `slopmortem/stages/__init__.py`**

Edit `slopmortem/stages/__init__.py`. Add the two re-exports and update `__all__`:

```python
from slopmortem.stages.llm_rerank import (
    llm_rerank as llm_rerank,
)
from slopmortem.stages.llm_rerank import (
    select_top_n_by_similarity as select_top_n_by_similarity,
)
```

```python
from slopmortem.stages.synthesize import (
    drop_below_min_similarity as drop_below_min_similarity,
)
from slopmortem.stages.synthesize import (
    synthesize as synthesize,
)
from slopmortem.stages.synthesize import (
    synthesize_all as synthesize_all,
)
from slopmortem.stages.synthesize import (
    synthesize_prompt_kwargs as synthesize_prompt_kwargs,
)
```

Update `__all__`:

```python
__all__ = [
    "SparseEncoder",
    "consolidate_risks",
    "drop_below_min_similarity",
    "extract_facets",
    "llm_rerank",
    "retrieve",
    "select_top_n_by_similarity",
    "synthesize",
    "synthesize_all",
    "synthesize_prompt_kwargs",
]
```

- [x] **Step 2: Update `pipeline.py` imports**

Edit `slopmortem/pipeline.py`:

In the `from slopmortem.stages import (...)` block at lines 22-28, add the two new imports:

```python
from slopmortem.stages import (
    consolidate_risks,
    drop_below_min_similarity,
    extract_facets,
    llm_rerank,
    retrieve,
    select_top_n_by_similarity,
    synthesize_all,
)
```

Drop the now-unused names from `TYPE_CHECKING`. After the edit, the `TYPE_CHECKING` block should read:

```python
if TYPE_CHECKING:
    from slopmortem.budget import Budget
    from slopmortem.config import Config
    from slopmortem.corpus import Corpus
    from slopmortem.llm import EmbeddingClient, LLMClient
    from slopmortem.models import InputContext
    from slopmortem.stages import SparseEncoder
```

(Removed: `Candidate`, `ScoredCandidate`, `SimilarityScores` — they're only referenced by the deleted helpers.)

- [x] **Step 3: Delete the policy helpers from `pipeline.py`**

Delete the following blocks from `slopmortem/pipeline.py`:
- `_mean_similarity_score` (lines 90-97)
- `_filter_by_min_similarity` (lines 100-103)
- `_filter_synth_by_min_similarity` (lines 106-111)
- `_log_min_similarity_drop` (lines 114-123)
- `_select_top_n` (lines 126-143)
- `_join_to_candidates` (lines 146-157)

Leave `cutoff_iso` (lines 79-87) and `_current_trace_id` (lines 160-167) in place — they are pipeline-only.

- [x] **Step 4: Update the `run_query` body to call the new stage functions**

In `slopmortem/pipeline.py`, replace the `_select_top_n` call (currently around lines 254-259) with:

```python
        top_n, filtered_pre_synth = select_top_n_by_similarity(
            retrieved=retrieved,
            ranked=reranked.ranked,
            min_similarity=config.min_similarity_score,
            n_synthesize=config.N_synthesize,
        )
```

Replace the post-synth filter block (currently around lines 286-295) with:

```python
        successes = [s for s in synth_results if isinstance(s, Synthesis)]
        successes, filtered_post_synth = drop_below_min_similarity(
            successes, min_similarity=config.min_similarity_score
        )
```

(The `synth_in` local goes away; `drop_below_min_similarity` returns the dropped count directly. The log line moves into the stage function — `pipeline.py` no longer logs about min-similarity drops.)

- [x] **Step 5: Verify pipeline imports are still consistent**

Run: `uv run ruff check slopmortem/pipeline.py`
Expected: no unused-import warnings. If `Synthesis` (used in `[s for s in synth_results if isinstance(s, Synthesis)]`) is flagged, leave it — it's used. If anything else is flagged, the previous edits left a stale reference; remove it.

- [x] **Step 6: Run the full suite**

Run: `just lint && just typecheck && just test`
Expected: all green. End-to-end pipeline tests in `test_pipeline_e2e.py` (e.g. `test_run_query_zero_passes_threshold`, `test_post_synth_filter_records_drop_on_pipeline_meta`) verify the swap end-to-end — they exercise `run_query`, which now goes through the stage functions.

---

## Task 5: Create `ingest/_ports.py` and migrate symbols

**Files:**
- Create: `slopmortem/ingest/_ports.py`
- Modify: `slopmortem/ingest/_orchestrator.py`
- Modify: `slopmortem/ingest/__init__.py`
- Modify: `slopmortem/ingest/_ingest.py`
- Modify: `slopmortem/ingest/_fan_out.py`
- Modify: `slopmortem/ingest/_journal_writes.py`
- Modify: `slopmortem/ingest/_slop_gate.py`
- Modify: `tests/ingest/test_orchestrator_helpers.py`
- Modify: `.importlinter`

**Brief for the implementer:** Move the port surface (protocols, type alias, enums, `IngestProgress`, `NullProgress`, `IngestResult`, `_Point`) into a new `_ports.py`. Update every importer to point at the new module. Remove the moved symbols from `_orchestrator.py`. Register `_ports` as a private ingest module in `.importlinter` so the encapsulation contract picks it up immediately rather than three tasks from now. The other layers (`_helpers.py`, `_impls.py`) come in Tasks 6 and 7 — `_orchestrator.py` will still hold helpers and impls after this task.

**Anti-scope:** Do NOT move helpers (`_text_id_for`, `_skip_key`, `_truncate_to_tokens`, `_entry_summary_text`, `_enrich_pipeline`, `_gather_entries`, `_build_payload`, `_date_from_year`, `_RELIABILITY_RANK`). Do NOT move classes (`InMemoryCorpus`, `FakeSlopClassifier`, `HaikuSlopClassifier`). Do NOT delete `_orchestrator.py` yet — Task 8 owns that and the corresponding `_orchestrator` removal from `.importlinter`. Do NOT commit.

- [x] **Step 1: Create `slopmortem/ingest/_ports.py`**

Write the following to `slopmortem/ingest/_ports.py`:

```python
# pyright: reportAny=false
"""Port surface for the ingest package: protocols, type aliases, dataclasses, enums.

Leaf within the ingest package — imports nothing from sibling ingest modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "INGEST_PHASE_LABELS",
    "Corpus",
    "IngestPhase",
    "IngestProgress",
    "IngestResult",
    "NullProgress",
    "SlopClassifier",
    "SparseEncoder",
    "_Point",
]

type SparseEncoder = Callable[[str], dict[int, float]]

# Cap on indexed per-entry exception attributes so a pathological run can't
# blow past Laminar's per-span attribute limit. Beyond this we record only
# ``errors.truncated_count``.
_MAX_RECORDED_ERRORS: Final[int] = 50


@runtime_checkable
class Corpus(Protocol):
    """Narrow corpus surface ingest depends on; prod impl is :class:`QdrantCorpus`."""

    async def upsert_chunk(self, point: object) -> None: ...

    async def has_chunks(self, canonical_id: str) -> bool: ...

    async def delete_chunks_for_canonical(self, canonical_id: str) -> None: ...


class IngestPhase(StrEnum):
    GATHER = "gather"
    CLASSIFY = "classify"
    CACHE_WARM = "cache_warm"
    FAN_OUT = "fan_out"
    WRITE = "write"


# Keyed on IngestPhase so adding a phase fails type-check at every consumer
# until it gets a label here.
INGEST_PHASE_LABELS: dict[IngestPhase, str] = {
    IngestPhase.GATHER: "Gathering entries from sources",
    IngestPhase.CLASSIFY: "Classifying / slop-filtering",
    IngestPhase.CACHE_WARM: "Warming prompt cache",
    IngestPhase.FAN_OUT: "Facets + summarize fan-out",
    IngestPhase.WRITE: "Entity-resolve / chunk / qdrant",
}


@runtime_checkable
class IngestProgress(Protocol):
    """Phase-level progress hooks.

    Default :class:`NullProgress` keeps the orchestrator decoupled from any
    UI library; the CLI wires a Rich impl.
    """

    def start_phase(self, phase: IngestPhase, total: int | None) -> None:
        """``total=None`` marks the phase indeterminate (Rich pulses; ETA blank)."""

    def advance_phase(self, phase: IngestPhase, n: int = 1) -> None: ...

    def end_phase(self, phase: IngestPhase) -> None: ...

    def log(self, message: str) -> None: ...

    def error(self, phase: IngestPhase, message: str) -> None: ...


class NullProgress:
    """No-op :class:`IngestProgress` for when no display surface is attached."""

    def start_phase(self, phase: IngestPhase, total: int | None) -> None: ...

    def advance_phase(self, phase: IngestPhase, n: int = 1) -> None: ...

    def end_phase(self, phase: IngestPhase) -> None: ...

    def log(self, message: str) -> None: ...

    def error(self, phase: IngestPhase, message: str) -> None: ...


@runtime_checkable
class SlopClassifier(Protocol):
    """Score a document for LLM-generated-text likelihood; ``> threshold`` quarantines."""

    async def score(self, text: str) -> float: ...


@dataclass
class _Point:
    """Stand-in for a Qdrant point; prod uses ``qdrant_client.models.PointStruct``."""

    id: str
    vector: dict[str, object]
    payload: dict[str, object]


@dataclass
class IngestResult:
    seen: int = 0
    processed: int = 0
    quarantined: int = 0
    skipped: int = 0
    skipped_empty: int = 0
    failed: int = 0
    errors: int = 0
    source_failures: int = 0
    would_process: int = 0  # populated when dry_run=True
    dry_run: bool = False
    cache_warmed: bool = False
    cache_creation_tokens_warm: int = 0
    span_events: list[str] = field(default_factory=list)
```

- [x] **Step 2: Remove the migrated symbols from `_orchestrator.py`**

Edit `slopmortem/ingest/_orchestrator.py`:

Delete:
- `type SparseEncoder = ...`
- `_MAX_RECORDED_ERRORS` (the constant + its comment)
- `class Corpus(Protocol): ...`
- `class IngestPhase(StrEnum): ...`
- `INGEST_PHASE_LABELS` dict
- `class IngestProgress(Protocol): ...`
- `class NullProgress: ...`
- `class SlopClassifier(Protocol): ...`
- `class _Point: ...`
- `class IngestResult: ...`

Update the module docstring's first line to: `"""Helpers, classifier impls, and the in-memory corpus stand-in for the ingest package."""`

Trim `__all__` to only the symbols still in the file: `["FakeSlopClassifier", "HaikuSlopClassifier", "InMemoryCorpus"]`. Leave the helpers as `# pyright: ignore[reportUnusedFunction]` callers — `_text_id_for`, `_reliability_for`, `_skip_key`, `_entry_summary_text`, `_enrich_pipeline`, `_gather_entries`, `_build_payload`, `_date_from_year`, `_truncate_to_tokens` — they're consumed by sibling modules via direct private import, not via `__all__`.

Update `_orchestrator.py`'s imports — drop unused ones (`StrEnum`, `Final`, `Protocol`, `runtime_checkable`, `dataclass`, `field` only-as-needed). Leave `Callable`, `Sequence` etc. that the helpers still need.

The `_gather_entries` helper currently references `IngestPhase`, `IngestProgress`, and `NullProgress` — add an import at the top of `_orchestrator.py`:

```python
from slopmortem.ingest._ports import IngestPhase, NullProgress

if TYPE_CHECKING:
    ...
    from slopmortem.ingest._ports import IngestProgress
```

- [x] **Step 3: Update `slopmortem/ingest/__init__.py`**

Edit `slopmortem/ingest/__init__.py`. Replace the per-symbol re-export block with:

```python
"""Ingest pipeline: gather → slop classify → fan-out → embed → upsert."""

from __future__ import annotations

from slopmortem.ingest._ingest import (
    ingest as ingest,
)
from slopmortem.ingest._orchestrator import (
    FakeSlopClassifier as FakeSlopClassifier,
)
from slopmortem.ingest._orchestrator import (
    HaikuSlopClassifier as HaikuSlopClassifier,
)
from slopmortem.ingest._orchestrator import (
    InMemoryCorpus as InMemoryCorpus,
)
from slopmortem.ingest._ports import (
    INGEST_PHASE_LABELS as INGEST_PHASE_LABELS,
)
from slopmortem.ingest._ports import (
    Corpus as Corpus,
)
from slopmortem.ingest._ports import (
    IngestPhase as IngestPhase,
)
from slopmortem.ingest._ports import (
    IngestResult as IngestResult,
)
from slopmortem.ingest._ports import (
    SlopClassifier as SlopClassifier,
)
from slopmortem.ingest._ports import (
    _Point as _Point,
)

__all__ = [
    "INGEST_PHASE_LABELS",
    "Corpus",
    "FakeSlopClassifier",
    "HaikuSlopClassifier",
    "InMemoryCorpus",
    "IngestPhase",
    "IngestResult",
    "SlopClassifier",
    "_Point",
    "ingest",
]
```

(`InMemoryCorpus`, `FakeSlopClassifier`, `HaikuSlopClassifier` keep their `_orchestrator` source for now — Task 7 moves them.)

- [x] **Step 4: Update `slopmortem/ingest/_ingest.py` imports**

Edit `slopmortem/ingest/_ingest.py`:

Replace the `from slopmortem.ingest._orchestrator import (...)` block (lines 22-30) with two imports:

```python
from slopmortem.ingest._orchestrator import (
    _enrich_pipeline,  # pyright: ignore[reportPrivateUsage]  -- intra-package private boundary
    _entry_summary_text,  # pyright: ignore[reportPrivateUsage]  -- intra-package private boundary
    _gather_entries,  # pyright: ignore[reportPrivateUsage]  -- intra-package private boundary
)
from slopmortem.ingest._ports import (
    IngestPhase,
    IngestResult,
    NullProgress,
)
from slopmortem.ingest._ports import (
    _MAX_RECORDED_ERRORS,  # pyright: ignore[reportPrivateUsage]  -- intra-package private boundary
)
```

Update the `TYPE_CHECKING` block (currently `tests/...`/`slopmortem/...`):

```python
if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from slopmortem.budget import Budget
    from slopmortem.config import Config
    from slopmortem.corpus import MergeJournal
    from slopmortem.corpus.sources import Enricher, Source
    from slopmortem.ingest._ports import (
        Corpus,
        IngestProgress,
        SlopClassifier,
        SparseEncoder,
    )
    from slopmortem.llm import EmbeddingClient, LLMClient
    from slopmortem.models import RawEntry
```

- [x] **Step 5: Update `slopmortem/ingest/_fan_out.py` imports**

Edit `slopmortem/ingest/_fan_out.py`. Replace the `from slopmortem.ingest._orchestrator import (...)` block (lines 17-24) with:

```python
from slopmortem.ingest._orchestrator import (
    _text_id_for,  # pyright: ignore[reportPrivateUsage]  -- intra-package private boundary
)
from slopmortem.ingest._ports import (
    IngestPhase,
    IngestProgress,
    NullProgress,
    SparseEncoder,
    _Point,
)
```

Update the `TYPE_CHECKING` block to import `Corpus` from `_ports`:

```python
if TYPE_CHECKING:
    from collections.abc import Sequence

    from slopmortem.config import Config
    from slopmortem.ingest._ports import Corpus
    from slopmortem.llm import EmbeddingClient, LLMClient
    from slopmortem.models import CandidatePayload, Facets, RawEntry
```

- [x] **Step 6: Update `slopmortem/ingest/_journal_writes.py` imports**

Edit `slopmortem/ingest/_journal_writes.py`. The current block at lines 34-40:

```python
from slopmortem.ingest._orchestrator import (
    SparseEncoder,
    _build_payload,
    _reliability_for,
    _skip_key,
    _text_id_for,
)
```

becomes:

```python
from slopmortem.ingest._orchestrator import (
    _build_payload,  # pyright: ignore[reportPrivateUsage]  -- intra-package private boundary
    _reliability_for,  # pyright: ignore[reportPrivateUsage]  -- intra-package private boundary
    _skip_key,  # pyright: ignore[reportPrivateUsage]  -- intra-package private boundary
    _text_id_for,  # pyright: ignore[reportPrivateUsage]  -- intra-package private boundary
)
from slopmortem.ingest._ports import SparseEncoder
```

Update the `TYPE_CHECKING` block to import `Corpus` from `_ports`:

```python
if TYPE_CHECKING:
    from pathlib import Path

    from slopmortem.config import Config
    from slopmortem.corpus import MergeJournal
    from slopmortem.ingest._fan_out import _FanoutResult
    from slopmortem.ingest._ports import Corpus
    from slopmortem.llm import EmbeddingClient, LLMClient
    from slopmortem.models import RawEntry
```

- [x] **Step 7: Update `slopmortem/ingest/_slop_gate.py` imports**

Edit `slopmortem/ingest/_slop_gate.py`. The TYPE_CHECKING block at lines 20-26 has:

```python
    from slopmortem.ingest._orchestrator import SlopClassifier
```

Change to:

```python
    from slopmortem.ingest._ports import SlopClassifier
```

- [x] **Step 8: Update `tests/ingest/test_orchestrator_helpers.py` imports**

Edit `tests/ingest/test_orchestrator_helpers.py`. The current block at lines 19-24:

```python
from slopmortem.ingest._orchestrator import (
    NullProgress,
    _entry_summary_text,
    _gather_entries,
    _truncate_to_tokens,
)
```

becomes:

```python
from slopmortem.ingest._orchestrator import (
    _entry_summary_text,  # pyright: ignore[reportPrivateUsage]  -- module-private test
    _gather_entries,  # pyright: ignore[reportPrivateUsage]  -- module-private test
    _truncate_to_tokens,  # pyright: ignore[reportPrivateUsage]  -- module-private test
)
from slopmortem.ingest._ports import NullProgress
```

(Tasks 6 and 7 will pull `_entry_summary_text` / `_gather_entries` / `_truncate_to_tokens` to `_helpers.py` — left alone here.)

- [x] **Step 9: Register `_ports` in `.importlinter`**

Edit `.importlinter`. Locate the `[importlinter:contract:ingest-private]` block. Add `slopmortem.ingest._ports` to `forbidden_modules` so the encapsulation contract starts enforcing it now rather than waiting for Task 8. The block becomes:

```ini
forbidden_modules =
    slopmortem.ingest._fan_out
    slopmortem.ingest._ingest
    slopmortem.ingest._journal_writes
    slopmortem.ingest._orchestrator
    slopmortem.ingest._ports
    slopmortem.ingest._slop_gate
    slopmortem.ingest._warm_cache
```

(`_orchestrator` stays on the list until Task 8 deletes the file. `_helpers` and `_impls` are added by Tasks 6 and 7.)

- [x] **Step 10: Run the full suite**

Run: `just lint && just typecheck && just test`
Expected: all green. `just lint` invokes `lint-imports` (per `justfile:74-77`), so the contract update is verified end-of-task. The package facade still exports the same surface; private intra-package imports now route through `_ports.py`.

---

## Task 6: Create `ingest/_helpers.py` and migrate helpers

**Files:**
- Create: `slopmortem/ingest/_helpers.py`
- Modify: `slopmortem/ingest/_orchestrator.py`
- Modify: `slopmortem/ingest/_ingest.py`
- Modify: `slopmortem/ingest/_fan_out.py`
- Modify: `slopmortem/ingest/_journal_writes.py`
- Modify: `tests/ingest/test_orchestrator_helpers.py`
- Modify: `.importlinter`

**Brief for the implementer:** Pull the pure helpers (`_text_id_for`, `_reliability_for`, `_skip_key`, `_truncate_to_tokens`, `_entry_summary_text`, `_enrich_pipeline`, `_gather_entries`, `_build_payload`, `_date_from_year`) and `_RELIABILITY_RANK` into a new `_helpers.py`. Update every importer. Remove the helpers from `_orchestrator.py`. Register `_helpers` in `.importlinter` in this task.

**Anti-scope:** Do NOT move classifiers or `InMemoryCorpus` — Task 7 owns them. Do NOT delete `_orchestrator.py` — Task 8 owns the deletion. Do NOT change helper signatures or behaviour. Do NOT inline `_MAX_RECORDED_ERRORS`; it stays in `_ports.py` as already migrated. Do NOT commit.

- [x] **Step 1: Create `slopmortem/ingest/_helpers.py`**

Write to `slopmortem/ingest/_helpers.py`:

```python
# pyright: reportAny=false
"""Pure helpers for ingest: hashing, truncation, payload assembly, gather loop.

No I/O state: every helper takes its dependencies as parameters. No imports
from sibling ingest submodules except :mod:`_ports`.
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING, Final

from slopmortem.corpus import extract_clean
from slopmortem.ingest._ports import IngestPhase, NullProgress
from slopmortem.models import CandidatePayload
from slopmortem.tracing import SpanEvent

if TYPE_CHECKING:
    from collections.abc import Sequence

    from slopmortem.corpus.sources import Enricher, Source
    from slopmortem.ingest._ports import IngestProgress
    from slopmortem.models import Facets, RawEntry

__all__ = [
    "_build_payload",
    "_date_from_year",
    "_enrich_pipeline",
    "_entry_summary_text",
    "_gather_entries",
    "_reliability_for",
    "_skip_key",
    "_text_id_for",
    "_truncate_to_tokens",
]

logger = logging.getLogger(__name__)

# merge_text orders sections by this. Curated > HN > Wayback > everything else.
_RELIABILITY_RANK: Final[dict[str, int]] = {
    "curated": 0,
    "hn": 1,
    "wayback": 2,
    "crunchbase": 3,
}


def _text_id_for(canonical_id: str) -> str:
    return hashlib.sha256(canonical_id.encode("utf-8")).hexdigest()[:16]


def _reliability_for(source: str) -> int:
    return _RELIABILITY_RANK.get(source, 9)


def _skip_key(  # noqa: PLR0913 - the contract tuple is wide
    *,
    content_hash: str,
    facet_sha: str,
    summarize_sha: str,
    haiku_model_id: str,
    embed_model_id: str,
    chunk_strategy: str,
    taxonomy_version: str,
    reliability_rank_version: str,
) -> str:
    raw = (
        f"{content_hash}|{facet_sha}|{summarize_sha}|"
        f"{haiku_model_id}|{embed_model_id}|{chunk_strategy}|"
        f"{taxonomy_version}|{reliability_rank_version}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate *text* to *max_tokens* via cl100k_base.

    Anthropic's tokenizer isn't published; cl100k_base agrees within ~10%
    on English prose, well inside the truncation budget's headroom.
    """
    if max_tokens <= 0:
        return text
    import tiktoken  # noqa: PLC0415 - heavy dep; lazy

    enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return enc.decode(tokens[:max_tokens])


def _entry_summary_text(entry: RawEntry, *, max_tokens: int) -> str:
    """Return entry body text, clipped to *max_tokens*.

    Clipped to bound LLM input cost on long-tail articles (Wikipedia entries
    can run 60KB+ after trafilatura).
    """
    if entry.markdown_text:
        return _truncate_to_tokens(entry.markdown_text, max_tokens)
    if entry.raw_html:
        return _truncate_to_tokens(extract_clean(entry.raw_html), max_tokens)
    return ""


async def _enrich_pipeline(entry: RawEntry, enrichers: Sequence[Enricher]) -> RawEntry:
    cur = entry
    for e in enrichers:
        cur = await e.enrich(cur)
    return cur


async def _gather_entries(
    sources: Sequence[Source],
    *,
    span_events: list[str],
    limit: int | None = None,
    progress: IngestProgress | None = None,
) -> tuple[list[RawEntry], int]:
    """Per-source failures are logged and counted, never abort the run.

    ``--limit`` is a real fast-path knob, not a post-gather slice: sources
    beyond the cap aren't started, and in-progress sources break out of their
    async iterator on the next yield.
    """
    out: list[RawEntry] = []
    failures = 0
    bar = progress or NullProgress()
    for src in sources:
        if limit is not None and len(out) >= limit:
            break
        try:
            iterable = src.fetch()
            async for entry in iterable:
                out.append(entry)
                bar.advance_phase(IngestPhase.GATHER)
                if limit is not None and len(out) >= limit:
                    break
        except Exception as exc:  # noqa: BLE001 - never abort the run on a per-source failure.
            logger.warning(
                "ingest: source %r failed: %s",
                type(src).__name__,
                exc,
            )
            span_events.append(SpanEvent.SOURCE_FETCH_FAILED.value)
            failures += 1
    return out, failures


def _build_payload(  # noqa: PLR0913 - payload assembly takes every store-time field
    *,
    facets: Facets,
    summary: str,
    body: str,
    slop_score: float,
    sources_seen: list[str],
    provenance_id: str,
    text_id: str,
    name: str,
    provenance: str,
) -> CandidatePayload:
    founding_year = facets.founding_year
    failure_year = facets.failure_year
    return CandidatePayload(
        name=name,
        summary=summary,
        body=body,
        facets=facets,
        founding_date=None if founding_year is None else _date_from_year(founding_year),
        failure_date=None if failure_year is None else _date_from_year(failure_year),
        founding_date_unknown=founding_year is None,
        failure_date_unknown=failure_year is None,
        provenance="curated_real" if provenance == "curated" else "scraped",
        slop_score=slop_score,
        sources=sources_seen,
        provenance_id=provenance_id,
        text_id=text_id,
    )


def _date_from_year(year: int):  # noqa: ANN202 - narrow internal helper
    from datetime import date  # noqa: PLC0415

    return date(year, 1, 1)
```

The helpers move byte-for-byte. No signature, annotation, or import-style change in this task — incidental cleanups belong in a separate post-move pass if anyone wants them.

- [x] **Step 2: Remove the migrated helpers from `_orchestrator.py`**

Edit `slopmortem/ingest/_orchestrator.py`. Delete:
- `_RELIABILITY_RANK` constant
- `_text_id_for`
- `_reliability_for`
- `_skip_key`
- `_truncate_to_tokens`
- `_entry_summary_text`
- `_enrich_pipeline`
- `_gather_entries`
- `_build_payload`
- `_date_from_year`

Drop now-unused imports from `_orchestrator.py`: `hashlib`, `logging`, `dataclass`/`field` (only if no remaining class uses them — `InMemoryCorpus`, `FakeSlopClassifier`, `HaikuSlopClassifier` all do, so keep `dataclass`/`field`), `extract_clean` (was only used by `_entry_summary_text`), `Final`, `Callable` from TYPE_CHECKING, `Source`/`Enricher` from TYPE_CHECKING, `RawEntry` from TYPE_CHECKING, `SpanEvent`, `IngestPhase` (no longer needed), `NullProgress`. Run `ruff check` to confirm none of these are still referenced before deleting.

The remaining `_orchestrator.py` should hold only `InMemoryCorpus`, `FakeSlopClassifier`, `HaikuSlopClassifier`, plus their imports (`json`, `dataclass`/`field`, `cast`, `LLMClient` (TYPE_CHECKING), `prompt_template_sha`, `render_prompt`, `_Point` (from `_ports`)). Update its docstring to: `"""Slop classifier impls and the in-memory corpus stand-in. Task 7 will move these to ``_impls.py``."""`. Update `__all__`: `["FakeSlopClassifier", "HaikuSlopClassifier", "InMemoryCorpus"]`.

- [x] **Step 3: Re-route `_ingest.py` helper imports**

Edit `slopmortem/ingest/_ingest.py`. Replace:

```python
from slopmortem.ingest._orchestrator import (
    _enrich_pipeline,
    _entry_summary_text,
    _gather_entries,
)
```

with:

```python
from slopmortem.ingest._helpers import (
    _enrich_pipeline,  # pyright: ignore[reportPrivateUsage]  -- intra-package private boundary
    _entry_summary_text,  # pyright: ignore[reportPrivateUsage]  -- intra-package private boundary
    _gather_entries,  # pyright: ignore[reportPrivateUsage]  -- intra-package private boundary
)
```

- [x] **Step 4: Re-route `_fan_out.py` helper import**

Edit `slopmortem/ingest/_fan_out.py`. Replace:

```python
from slopmortem.ingest._orchestrator import (
    _text_id_for,
)
```

with:

```python
from slopmortem.ingest._helpers import (
    _text_id_for,  # pyright: ignore[reportPrivateUsage]  -- intra-package private boundary
)
```

- [x] **Step 5: Re-route `_journal_writes.py` helper imports**

Edit `slopmortem/ingest/_journal_writes.py`. Replace:

```python
from slopmortem.ingest._orchestrator import (
    _build_payload,
    _reliability_for,
    _skip_key,
    _text_id_for,
)
```

with:

```python
from slopmortem.ingest._helpers import (
    _build_payload,  # pyright: ignore[reportPrivateUsage]  -- intra-package private boundary
    _reliability_for,  # pyright: ignore[reportPrivateUsage]  -- intra-package private boundary
    _skip_key,  # pyright: ignore[reportPrivateUsage]  -- intra-package private boundary
    _text_id_for,  # pyright: ignore[reportPrivateUsage]  -- intra-package private boundary
)
```

- [x] **Step 6: Re-route `tests/ingest/test_orchestrator_helpers.py` imports**

Edit `tests/ingest/test_orchestrator_helpers.py`. Replace:

```python
from slopmortem.ingest._orchestrator import (
    _entry_summary_text,
    _gather_entries,
    _truncate_to_tokens,
)
```

with:

```python
from slopmortem.ingest._helpers import (
    _entry_summary_text,  # pyright: ignore[reportPrivateUsage]  -- module-private test
    _gather_entries,  # pyright: ignore[reportPrivateUsage]  -- module-private test
    _truncate_to_tokens,  # pyright: ignore[reportPrivateUsage]  -- module-private test
)
```

- [x] **Step 7: Register `_helpers` in `.importlinter`**

Edit `.importlinter`. Add `slopmortem.ingest._helpers` to the `[importlinter:contract:ingest-private]` `forbidden_modules` list:

```ini
forbidden_modules =
    slopmortem.ingest._fan_out
    slopmortem.ingest._helpers
    slopmortem.ingest._ingest
    slopmortem.ingest._journal_writes
    slopmortem.ingest._orchestrator
    slopmortem.ingest._ports
    slopmortem.ingest._slop_gate
    slopmortem.ingest._warm_cache
```

- [x] **Step 8: Run the full suite**

Run: `just lint && just typecheck && just test`
Expected: all green. `_orchestrator.py` now holds only the three impl classes.

---

## Task 7: Create `ingest/_impls.py` and migrate impl classes

**Files:**
- Create: `slopmortem/ingest/_impls.py`
- Modify: `slopmortem/ingest/_orchestrator.py`
- Modify: `slopmortem/ingest/__init__.py`
- Modify: `.importlinter`

**Brief for the implementer:** Move `InMemoryCorpus`, `FakeSlopClassifier`, `HaikuSlopClassifier` into a new `_impls.py`. Update the `slopmortem.ingest` facade to re-export from there. Register `_impls` in `.importlinter`. After this task, `_orchestrator.py` is empty.

**Anti-scope:** Do NOT delete `_orchestrator.py` yet — Task 8 owns that and the corresponding `_orchestrator` removal from `.importlinter`. Do NOT touch the test files; their imports go via the package facade. Do NOT commit.

- [x] **Step 1: Create `slopmortem/ingest/_impls.py`**

Write to `slopmortem/ingest/_impls.py`:

```python
# pyright: reportAny=false
"""Runtime implementations of the ingest ports.

`InMemoryCorpus` is for tests. `FakeSlopClassifier` is for tests and dry-run.
`HaikuSlopClassifier` is the production slop classifier.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from slopmortem.ingest._ports import _Point
from slopmortem.llm import prompt_template_sha, render_prompt

if TYPE_CHECKING:
    from slopmortem.llm import LLMClient

__all__ = [
    "FakeSlopClassifier",
    "HaikuSlopClassifier",
    "InMemoryCorpus",
]


@dataclass
class InMemoryCorpus:
    """In-memory :class:`Corpus` for tests; not used in production."""

    points: list[_Point] = field(default_factory=list)

    async def upsert_chunk(self, point: object) -> None:
        if not isinstance(point, _Point):
            msg = f"InMemoryCorpus expects _Point, got {type(point).__name__}"
            raise TypeError(msg)
        self.points.append(point)

    async def has_chunks(self, canonical_id: str) -> bool:
        return any(p.payload.get("canonical_id") == canonical_id for p in self.points)

    async def delete_chunks_for_canonical(self, canonical_id: str) -> None:
        self.points = [p for p in self.points if p.payload.get("canonical_id") != canonical_id]


@dataclass
class FakeSlopClassifier:
    """Deterministic test :class:`SlopClassifier`; ``scores`` overrides by text-key prefix."""

    default_score: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)

    async def score(self, text: str) -> float:
        for key, val in self.scores.items():
            if text.startswith(key) or key in text:
                return val
        return self.default_score


@dataclass
class HaikuSlopClassifier:
    """LLM-backed slop classifier.

    Asks Haiku whether a text describes a dead company; returns 0.0 if yes,
    else 1.0 (above the default ``slop_threshold=0.7``, so quarantines).

    ``char_limit=6000`` so the demise narrative falls inside the window for long
    obituaries (Sun, WeWork). Tighter 1500-char caps caused false-negative
    quarantines.
    """

    llm: LLMClient
    model: str
    char_limit: int = 6000
    max_tokens: int | None = None

    async def score(self, text: str) -> float:
        snippet = text[: self.char_limit]
        prompt = render_prompt("slop_judge", text=snippet)
        result = await self.llm.complete(
            prompt,
            model=self.model,
            cache=False,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "SlopJudge",
                    "schema": {
                        "type": "object",
                        "properties": {"is_dead_company": {"type": "boolean"}},
                        "required": ["is_dead_company"],
                        "additionalProperties": False,
                    },
                    "strict": True,
                },
            },
            extra_body={"prompt_template_sha": prompt_template_sha("slop_judge")},
            max_tokens=self.max_tokens,
        )
        try:
            parsed: object = json.loads(result.text)
        except json.JSONDecodeError:
            # Conservative on parse failure: keep the entry rather than silently drop.
            return 0.0
        if not isinstance(parsed, dict):
            return 1.0
        is_dead = cast("dict[str, object]", parsed).get("is_dead_company")
        return 0.0 if is_dead is True else 1.0
```

- [x] **Step 2: Empty `_orchestrator.py`**

Edit `slopmortem/ingest/_orchestrator.py` so the file is reduced to a deprecation note (Task 8 deletes it):

```python
"""Empty after the orchestrator split. Task 8 deletes this file.

See ``_ports.py``, ``_helpers.py``, ``_impls.py`` for the new homes.
"""
```

- [x] **Step 3: Update `slopmortem/ingest/__init__.py` to re-export from `_impls.py`**

Edit `slopmortem/ingest/__init__.py`. Change the three impl re-exports' source to `_impls`:

```python
from slopmortem.ingest._impls import (
    FakeSlopClassifier as FakeSlopClassifier,
)
from slopmortem.ingest._impls import (
    HaikuSlopClassifier as HaikuSlopClassifier,
)
from slopmortem.ingest._impls import (
    InMemoryCorpus as InMemoryCorpus,
)
```

The full `__init__.py` after this edit:

```python
"""Ingest pipeline: gather → slop classify → fan-out → embed → upsert."""

from __future__ import annotations

from slopmortem.ingest._impls import (
    FakeSlopClassifier as FakeSlopClassifier,
)
from slopmortem.ingest._impls import (
    HaikuSlopClassifier as HaikuSlopClassifier,
)
from slopmortem.ingest._impls import (
    InMemoryCorpus as InMemoryCorpus,
)
from slopmortem.ingest._ingest import (
    ingest as ingest,
)
from slopmortem.ingest._ports import (
    INGEST_PHASE_LABELS as INGEST_PHASE_LABELS,
)
from slopmortem.ingest._ports import (
    Corpus as Corpus,
)
from slopmortem.ingest._ports import (
    IngestPhase as IngestPhase,
)
from slopmortem.ingest._ports import (
    IngestResult as IngestResult,
)
from slopmortem.ingest._ports import (
    SlopClassifier as SlopClassifier,
)
from slopmortem.ingest._ports import (
    _Point as _Point,
)

__all__ = [
    "INGEST_PHASE_LABELS",
    "Corpus",
    "FakeSlopClassifier",
    "HaikuSlopClassifier",
    "InMemoryCorpus",
    "IngestPhase",
    "IngestResult",
    "SlopClassifier",
    "_Point",
    "ingest",
]
```

- [x] **Step 4: Register `_impls` in `.importlinter`**

Edit `.importlinter`. Add `slopmortem.ingest._impls` to the `[importlinter:contract:ingest-private]` `forbidden_modules` list:

```ini
forbidden_modules =
    slopmortem.ingest._fan_out
    slopmortem.ingest._helpers
    slopmortem.ingest._impls
    slopmortem.ingest._ingest
    slopmortem.ingest._journal_writes
    slopmortem.ingest._orchestrator
    slopmortem.ingest._ports
    slopmortem.ingest._slop_gate
    slopmortem.ingest._warm_cache
```

- [x] **Step 5: Run the full suite**

Run: `just lint && just typecheck && just test`
Expected: all green. `_orchestrator.py` is now a stub; every consumer goes through `_ports`, `_helpers`, or `_impls` (or the package facade).

---

## Task 8: Delete `_orchestrator.py`, update `.importlinter`, rename test file

**Files:**
- Delete: `slopmortem/ingest/_orchestrator.py`
- Modify: `.importlinter`
- Rename: `tests/ingest/test_orchestrator_helpers.py` → `tests/ingest/test_ingest_internals.py`
- Modify: `docs/architecture.md` (only if it names `_orchestrator.py` directly — grep first)

**Brief for the implementer:** Final cleanup. Delete the stub `_orchestrator.py`. Remove only the `_orchestrator` line from `.importlinter`'s `ingest-private` contract (the new modules `_ports`, `_helpers`, `_impls` were registered incrementally in Tasks 5–7). Rename the test file (its old name references a deleted module). Skim `docs/architecture.md` for a literal `_orchestrator.py` mention; update only that mention if present.

**Anti-scope:** Do NOT add new tests, type stubs, or `__init__.py` reorganisation. Do NOT touch the rest of `.importlinter` (only the ingest-private contract change). Do NOT rewrite `docs/architecture.md` beyond a literal find-and-replace. Do NOT commit.

- [x] **Step 1: Verify `_orchestrator.py` has no remaining importers**

Run: `rg "from slopmortem\.ingest\._orchestrator" --no-heading`
Expected: zero matches across `slopmortem/` and `tests/`.

If the grep returns hits, stop and re-route them to `_ports.py`, `_helpers.py`, or `_impls.py` per the symbol's new home before deleting.

- [x] **Step 2: Delete `slopmortem/ingest/_orchestrator.py`**

Run: `rm slopmortem/ingest/_orchestrator.py`

- [x] **Step 3: Remove `_orchestrator` from `.importlinter`**

Edit `.importlinter`. In the `[importlinter:contract:ingest-private]` block, delete the `slopmortem.ingest._orchestrator` line from `forbidden_modules`. After the edit, the list should read:

```ini
forbidden_modules =
    slopmortem.ingest._fan_out
    slopmortem.ingest._helpers
    slopmortem.ingest._impls
    slopmortem.ingest._ingest
    slopmortem.ingest._journal_writes
    slopmortem.ingest._ports
    slopmortem.ingest._slop_gate
    slopmortem.ingest._warm_cache
```

(`_ports`, `_helpers`, `_impls` were added in Tasks 5–7 as each module was created. This task only removes the now-obsolete `_orchestrator` entry.)

- [x] **Step 4: Rename the test file**

Run: `mv tests/ingest/test_orchestrator_helpers.py tests/ingest/test_ingest_internals.py`

(Plain `mv`, not `git mv` — `git mv` stages the rename, which the subagent rule forbids. The parent agent stages the rename as part of the commit it authors.)

Update the test file's module docstring (top of file, line 1):

```python
"""Edge-case branches for ingest port stand-ins, helpers, and classifier impls."""
```

- [x] **Step 5: Search `docs/architecture.md` for direct mentions**

Run: `rg -n "_orchestrator" docs/`
Expected: zero matches, or only matches that are clearly unrelated.

If a literal `_orchestrator.py` reference appears in `docs/architecture.md`, update it to name the new module(s) (`_ports.py`, `_helpers.py`, `_impls.py` — pick the one(s) the docs section was talking about). If the docs reference is generic (e.g. "the orchestrator module"), update prose to reflect the new structure.

- [x] **Step 6: Run the import linter explicitly**

Run: `uv run lint-imports`
Expected: all contracts pass. (`just lint` invokes `lint-imports` — see `justfile:74-77` — so this is also covered by the next step. Running it standalone here surfaces import-contract failures before mixing them with ruff/format output.)

- [x] **Step 7: Run the full suite**

Run: `just lint && just typecheck && just test`
Expected: all green.

- [x] **Step 8: Final sanity: verify file sizes**

Run: `wc -l slopmortem/pipeline.py slopmortem/ingest/_ports.py slopmortem/ingest/_helpers.py slopmortem/ingest/_impls.py`
Expected:
- `pipeline.py` is meaningfully smaller than the starting 334 lines (rough target: 250-280 lines after Task 4 removed ~80 lines of policy helpers).
- `_ports.py`, `_helpers.py`, `_impls.py` together cover the original 379-line `_orchestrator.py` content, none of them appearing as a new junk drawer (rough target: each under 200 lines).

This is a sanity check, not an acceptance gate; if anything looks oddly bloated, scan the diff for accidental duplication.

---

## Task 9: Post-implementation polish

**Files:** No specific files — the polish skill chooses targets from the diff produced by Tasks 1–8.

**Brief for the implementer:** Invoke the `post-implementation-polish` skill against the cumulative diff of Tasks 1–8. The skill runs three review rounds with fixes, an idiomatic-code pass, `/cleanup` with fixes, then strips AI comments and humanizes remaining valuable ones. This task is a single skill invocation, not an open-ended cleanup pass.

**Anti-scope:** Do NOT introduce behaviour changes, new dependencies, new tests, or refactors outside the existing diff. Do NOT touch `.importlinter`, `pyproject.toml`, `justfile`, or `slopmortem.toml`. Do NOT widen scope beyond the files Tasks 1–8 already touched. Do NOT commit.

- [x] **Step 1: Verify Tasks 1–8 are all green**

Run: `just lint && just typecheck && just test`
Expected: all green. If anything fails here, stop and fix the underlying task before invoking polish.

- [x] **Step 2: Invoke the polish skill**

Run the `post-implementation-polish` skill. Let it execute its built-in sequence (3 review rounds → idiomatic pass → `/cleanup` → AI-comment strip → humanize). Apply only fixes the skill itself proposes and that fall inside the Task 1–8 diff scope.

- [x] **Step 3: Run the full suite**

Run: `just lint && just typecheck && just test`
Expected: all green.

---

## Self-review notes (kept for the executor)

- **Spec coverage:** Both refactors named in the user's request map to tasks. Refactor 1 (pipeline policy → stages) covers Tasks 1-4. Refactor 2 (orchestrator split) covers Tasks 5-8.
- **Type consistency:**
  - `select_top_n_by_similarity(*, retrieved, ranked, min_similarity, n_synthesize) -> tuple[list[Candidate], int]` — used in Task 4's `pipeline.py` swap with the same kwarg names.
  - `drop_below_min_similarity(syntheses, *, min_similarity) -> tuple[list[Synthesis], int]` — used in Task 4's `pipeline.py` swap with the same kwarg name.
  - `SimilarityScores.mean()` returns `float` — consumed by both stage functions via `.perspective_scores.mean()` / `.similarity.mean()`.
- **Placeholder scan:** No "TODO", "fill in", or "similar to Task N" stubs. Every new code block is reproduced in full.
- **Behaviour preservation:** Log lines for "post-rerank" / "post-synth" drops survive (now emitted from the stage modules). The `filtered_pre_synth` and `filtered_post_synth` semantics on `PipelineMeta` are preserved bit-for-bit. The injection-marker contract in `synthesize.py` is untouched.
