# Eval Runner Cassette Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or `/team-feature` to implement this plan task-by-task, per the Execution Strategy below. Steps use checkbox (`- [ ]`) syntax — these are **persistent durable state**, not visual decoration. The executor edits the plan file in place: `- [ ]` → `- [x]` the instant a step verifies, before moving on. On resume (new session, crash, takeover), the executor scans existing `- [x]` marks and skips them — these steps are NOT redone. TodoWrite mirrors this state in-session; the plan file is the source of truth across sessions.

**Goal:** Wire `slopmortem/evals/runner.py` to the cassette loaders and ephemeral Qdrant infrastructure that already exist, so `just eval` runs offline against committed fixtures and produces a real baseline instead of `"acme"` placeholder rows.

**Architecture:** `_run_deterministic` and its helpers are deleted and replaced by `_run_cassettes`, which opens a `setup_ephemeral_qdrant` context, loads the per-row cassette dir under `tests/fixtures/cassettes/evals/<row_id>/`, and feeds `FakeLLMClient` + `FakeEmbeddingClient` + a closure-based sparse encoder into `pipeline.run_query`. The host allowlist for `all_sources_in_allowed_domains` is computed directly from `Synthesis.sources` (already populated from `CandidatePayload.sources` in `slopmortem/stages/synthesize.py:Synthesis.from_llm`), so no `Corpus` Protocol changes are needed. After `run_query` returns, scoring pre-fetches `body` per candidate via `corpus.get_post_mortem` (still needed because `Synthesis` doesn't carry the body). A new helper `load_row_fakes(scope_dir, cfg)` in `slopmortem/evals/cassettes.py` owns the per-row cassette plumbing so both the runner and the e2e test share one entry point.

**Tech Stack:** Python 3.13, `anyio`, Pydantic v2, `qdrant-client`, `pytest` + `pytest-xdist`, `basedpyright` (strict). The cassette loaders live in `slopmortem/evals/cassettes.py` and `slopmortem/llm/cassettes.py`; the ephemeral-Qdrant context manager in `slopmortem/evals/qdrant_setup.py`; recording in `slopmortem/evals/recording_helper.py`. None of these change shape in this plan; `slopmortem/evals/cassettes.py` gains one helper.

## Why no Corpus Protocol change

A previous draft of this plan added `lookup_sources` to the `Corpus` Protocol and to six implementations. Review found this redundant: `Synthesis.sources: list[str]` is always populated from `CandidatePayload.sources` in `Synthesis.from_llm` (`slopmortem/stages/synthesize.py:137`), and `all_sources_in_allowed_domains` already iterates `s.sources` (`slopmortem/evals/assertions.py:31-43`). Re-fetching the same data via `corpus.lookup_sources(s.candidate_id)` would round-trip Qdrant per candidate for nothing. The `body` for `claims_grounded_in_body` is genuinely not on `Synthesis` — that's why `corpus.get_post_mortem` is still called per candidate.

## Live-mode behavior change

`_run_live` previously passed an empty `eval_corpus` so `claims_grounded_in_body` was vacuously `True` against `body=None` for every candidate. After this plan, `_run_live` fetches the real body via `corpus.get_post_mortem`, so the assertion actually runs. This is intentional — both modes now share one scoring path. Operators running `--live` may see new failures that were silently masked before.

## Execution Strategy

Subagents (default), sequential dispatch. Each task runs as a fresh agent; the next task starts only after the previous task's review gate passes and the operator gates (where applicable) complete.

Reason: tasks 1, 4, and 5 touch overlapping module surfaces (runner → e2e test → docstring cleanup) and the operator gates (Tasks 2 and 3) sit between them. No parallel batching is possible. The user's standing preference is sequential anyway.

## Task Dependency Graph

- Task 1: depends on `none` → first batch (cassette runner wiring)
- Task 2: depends on `Task 1` → second batch (operator gate — re-record eval cassettes + regenerate baseline)
- Task 3: depends on `Task 2` → third batch (operator gate — record e2e cassettes)
- Task 4: depends on `Task 3` → fourth batch (e2e test migration)
- Task 5: depends on `Task 4` → fifth batch (cleanup is last)

Each batch runs one task. There is no parallelism in this plan.

## Agent Assignments

- Task 1: Runner cassette wiring → python-development:python-pro (Python)
- Task 2: OPERATOR (re-record eval cassettes + regenerate baseline) → human
- Task 3: OPERATOR (record e2e cassettes) → human
- Task 4: e2e test migration → python-development:python-pro (Python)
- Task 5: Cleanup → python-development:python-pro (Python)
- Polish: post-implementation-polish → python-development:python-pro (uniform Python diff)

## Subagent constraints

Per the user's standing preferences (also stated in `docs/specs/2026-05-01-eval-runner-cassette-fix-design.md`):

- No agent stages or commits (`git add`, `git commit`). The parent owns commit authorship.
- No work outside the explicit CREATE/MODIFY file list per task. If an agent finds a "small win" outside its ownership, it stops and reports rather than making the change.
- Sequential dispatch — one agent at a time, with a review gate between each.

---

## Task 1: Replace `_run_deterministic` with `_run_cassettes`

**Files:**
- Modify: `slopmortem/evals/runner.py` (delete deterministic helpers; add `_run_cassettes`; rewire scoring around `bodies_map`; update module docstring + argparse description; add scope validation in `main`)
- Modify: `slopmortem/evals/cassettes.py` (add `load_row_fakes` helper)
- Create: `tests/evals/test_runner_replay.py` (cassette-replay integration tests)
- Create: `tests/evals/test_runner_scoring.py` (pure-function unit tests for scoring helpers)

Do NOT touch `slopmortem/evals/runner.py:main` argparse surface — `--live`, `--record`, `--write-baseline`, `--scope`, `--max-cost-usd` keep their current shapes.

Do NOT bump `_BASELINE_VERSION`. The baseline envelope stays at version 1; no `corpus_fixture_sha256` or `recording_metadata` fields are added.

Do NOT change the `Corpus` Protocol or any implementation of it.

### 1A — Add the shared cassette helper

- [x] **Step 1: Add `load_row_fakes` to `slopmortem/evals/cassettes.py`**

Add this function (place it after the existing `load_embedding_cassettes` / `load_llm_cassettes` definitions). Both `_run_cassettes` (Task 1) and the migrated e2e test (Task 4) call it.

```python
def load_row_fakes(
    scope_dir: Path,
    cfg: Config,
) -> tuple[FakeLLMClient, FakeEmbeddingClient, Callable[[str], dict[int, float]]]:
    """Load cassettes from *scope_dir* and build the LLM/embed/sparse fakes for one row.

    Returns a tuple of ``(fake_llm, fake_embed, sparse_encoder)`` ready to pass
    into ``pipeline.run_query``. Raises ``NoCannedEmbeddingError`` from the
    sparse encoder closure on a cassette miss; the LLM/dense fakes raise their
    own miss errors at call time.
    """
    llm_canned: dict[tuple[str, str, str], FakeResponse | CompletionResult] = {
        k: FakeResponse(
            text=v.text,
            stop_reason=v.stop_reason,
            cost_usd=v.cost_usd,
            cache_read_tokens=v.cache_read_tokens,
            cache_creation_tokens=v.cache_creation_tokens,
        )
        for k, v in load_llm_cassettes(scope_dir).items()
    }
    dense_canned, sparse_canned = load_embedding_cassettes(scope_dir)
    fake_llm = FakeLLMClient(canned=llm_canned, default_model=cfg.model_synthesize)
    fake_embed = FakeEmbeddingClient(model=cfg.embed_model_id, canned=dense_canned)

    def cassette_sparse(text: str) -> dict[int, float]:
        key = embed_cassette_key(text=text, model="Qdrant/bm25")
        if key not in sparse_canned:
            msg = f"no sparse cassette for {key!r}"
            raise NoCannedEmbeddingError(msg)
        idx, vals = sparse_canned[key]
        return dict(zip(idx, vals, strict=True))

    return fake_llm, fake_embed, cassette_sparse
```

Add the necessary imports at the top of `slopmortem/evals/cassettes.py`:

```python
from slopmortem.config import Config
from slopmortem.llm.cassettes import embed_cassette_key
from slopmortem.llm.fake import CompletionResult, FakeLLMClient, FakeResponse
from slopmortem.llm.fake_embeddings import FakeEmbeddingClient
```

Extend the `TYPE_CHECKING` block with `Callable`:

```python
if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path
```

(`Path` may already be there; only add what's missing.)

- [x] **Step 2: Typecheck after the helper lands**

Run: `just typecheck`
Expected: PASS.

### 1B — Write tests for the cassette runner

- [x] **Step 3: Create `tests/evals/test_runner_replay.py`**

```python
"""Cassette-replay integration tests for the eval runner.

Most tests need an ephemeral Qdrant collection. The unknown-scope test exits
before any Qdrant call and runs without the marker so a Qdrant-less host can
still verify the validation gate.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from slopmortem.evals import runner

if TYPE_CHECKING:
    from _pytest.capture import CaptureFixture


@pytest.mark.requires_qdrant
async def test_runner_replay_passes_with_recorded_cassettes(tmp_path: Path) -> None:
    """End-to-end happy path: ephemeral Qdrant + committed cassette dir → exit 0 with non-empty rows."""
    dataset = tmp_path / "seed.jsonl"
    dataset.write_text(
        json.dumps({"name": "ledgermint", "description": "B2B SaaS that automates monthly close."})
        + "\n"
    )
    baseline = tmp_path / "baseline.json"

    with pytest.raises(SystemExit) as excinfo:
        runner.main(
            [
                "--dataset",
                str(dataset),
                "--baseline",
                str(baseline),
                "--write-baseline",
            ]
        )
    assert excinfo.value.code == 0
    parsed = json.loads(baseline.read_text())
    assert parsed["version"] == 1
    assert "ledgermint" in parsed["rows"]
    row = parsed["rows"]["ledgermint"]
    assert row["candidates_count"] > 0, "happy path must exercise at least one candidate"
    assert row["assertions"], "assertions map must be non-empty on the happy path"


@pytest.mark.requires_qdrant
async def test_runner_replay_fails_loud_on_missing_cassette_dir(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """Missing cassette dir → FAIL line printed AND candidates_count=0 in baseline."""
    dataset = tmp_path / "seed.jsonl"
    dataset.write_text(
        json.dumps({"name": "no-such-row", "description": "n/a"}) + "\n"
    )
    baseline = tmp_path / "baseline.json"

    with pytest.raises(SystemExit) as excinfo:
        runner.main(
            [
                "--dataset",
                str(dataset),
                "--baseline",
                str(baseline),
                "--write-baseline",
            ]
        )
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "FAIL no-such-row" in out, "missing cassette dir must print a FAIL line"
    parsed = json.loads(baseline.read_text())
    assert parsed["rows"]["no-such-row"]["candidates_count"] == 0
    assert parsed["rows"]["no-such-row"]["assertions"] == {}


async def test_runner_replay_unknown_scope_is_fatal(tmp_path: Path) -> None:
    """`--scope notarow` exits 2 because scope-validation runs before any Qdrant call.

    No requires_qdrant marker — the gate fires in main() before dispatch.
    """
    dataset = tmp_path / "seed.jsonl"
    dataset.write_text(json.dumps({"name": "ledgermint", "description": "n/a"}) + "\n")
    baseline = tmp_path / "baseline.json"

    with pytest.raises(SystemExit) as excinfo:
        runner.main(
            [
                "--dataset",
                str(dataset),
                "--baseline",
                str(baseline),
                "--scope",
                "notarow",
                "--write-baseline",
            ]
        )
    assert excinfo.value.code == 2


@pytest.mark.requires_qdrant
async def test_runner_replay_fails_loud_on_llm_cassette_miss(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """Mutating an LLM cassette key (e.g. corrupt the synthesize file) → NoCannedResponseError → FAIL line, candidates_count=0.

    Strategy: copy the committed ledgermint cassette dir into a tmp scope dir,
    rewrite one synthesize__*.json file with a deliberately-different prompt
    template hash, then invoke the runner against the tmp dir. Achieved by
    monkeypatching the hardcoded ``Path("tests/fixtures/cassettes/evals")`` via
    ``runner._CASSETTE_ROOT`` (introduced in Task 1 step 5 specifically so this
    test can target the tmp tree).
    """
    import shutil

    src = Path("tests/fixtures/cassettes/evals/ledgermint")
    dst_root = tmp_path / "cassettes_root"
    dst = dst_root / "ledgermint"
    shutil.copytree(src, dst)
    # Corrupt one synthesize cassette by rewriting its prompt-hash filename.
    synth = next(dst.glob("synthesize__*.json"))
    synth.rename(dst / "synthesize__deadbeefdeadbeef.json")

    dataset = tmp_path / "seed.jsonl"
    dataset.write_text(
        json.dumps({"name": "ledgermint", "description": "B2B SaaS that automates monthly close."})
        + "\n"
    )
    baseline = tmp_path / "baseline.json"
    monkeypatch_root = pytest.MonkeyPatch()
    try:
        monkeypatch_root.setattr(runner, "_CASSETTE_ROOT", dst_root)
        with pytest.raises(SystemExit) as excinfo:
            runner.main(
                ["--dataset", str(dataset), "--baseline", str(baseline), "--write-baseline"]
            )
    finally:
        monkeypatch_root.undo()
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "FAIL ledgermint: cassette miss" in out
    parsed = json.loads(baseline.read_text())
    assert parsed["rows"]["ledgermint"]["candidates_count"] == 0


@pytest.mark.requires_qdrant
async def test_runner_replay_malformed_cassette_is_run_level_failure(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """A cassette with garbage JSON → CassetteFormatError → exit 2 (run-level failure)."""
    import shutil

    src = Path("tests/fixtures/cassettes/evals/ledgermint")
    dst_root = tmp_path / "cassettes_root"
    dst = dst_root / "ledgermint"
    shutil.copytree(src, dst)
    next(dst.glob("synthesize__*.json")).write_text("{not valid json")

    dataset = tmp_path / "seed.jsonl"
    dataset.write_text(
        json.dumps({"name": "ledgermint", "description": "n/a"}) + "\n"
    )
    baseline = tmp_path / "baseline.json"
    monkeypatch_root = pytest.MonkeyPatch()
    try:
        monkeypatch_root.setattr(runner, "_CASSETTE_ROOT", dst_root)
        with pytest.raises(SystemExit) as excinfo:
            runner.main(
                ["--dataset", str(dataset), "--baseline", str(baseline), "--write-baseline"]
            )
    finally:
        monkeypatch_root.undo()
    assert excinfo.value.code == 2


@pytest.mark.requires_qdrant
async def test_switching_embed_model_id_produces_loud_cassette_miss(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bumping ``Config.embed_model_id`` between record and replay → embedding cassette miss → FAIL line."""
    monkeypatch.setenv("SLOPMORTEM_EMBED_MODEL_ID", "text-embedding-3-large")

    dataset = tmp_path / "seed.jsonl"
    dataset.write_text(
        json.dumps({"name": "ledgermint", "description": "n/a"}) + "\n"
    )
    baseline = tmp_path / "baseline.json"

    with pytest.raises(SystemExit) as excinfo:
        runner.main(
            ["--dataset", str(dataset), "--baseline", str(baseline), "--write-baseline"]
        )
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "FAIL ledgermint" in out
```

- [x] **Step 4: Create `tests/evals/test_runner_scoring.py`**

These are pure-function tests — no Qdrant, no requires_qdrant marker. They run in milliseconds and form the fast feedback loop for the scoring helpers.

```python
"""Pure-function unit tests for the eval-runner scoring helpers.

Decoupled from cassettes / Qdrant: builds Synthesis instances directly and
exercises ``_allowed_hosts_for_candidate`` / ``_score_synthesis`` /
``_score_report`` with hand-built ``bodies_map`` mappings.
"""

from __future__ import annotations

from datetime import date

from slopmortem.evals.runner import (
    _allowed_hosts_for_candidate,
    _score_report,
    _score_synthesis,
)
from slopmortem.models import (
    Facets,
    InputContext,
    PerspectiveScore,
    PipelineMeta,
    Report,
    Similarity,
    Synthesis,
)


def _facets() -> Facets:
    return Facets(
        sector="fintech",
        business_model="b2b_saas",
        customer_type="smb",
        geography="us",
        monetization="subscription_recurring",
    )


def _synthesis(*, candidate_id: str, sources: list[str]) -> Synthesis:
    sim_scores = {
        k: PerspectiveScore(score=5.0, rationale="x")
        for k in ("business_model", "market", "gtm", "stage_scale")
    }
    return Synthesis(
        candidate_id=candidate_id,
        name=candidate_id,
        one_liner="x",
        similarity=Similarity(**sim_scores),
        why_similar="x",
        where_diverged="differs in x",
        failure_causes=["a"],
        lessons_for_input=["b"],
        sources=sources,
        founding_date=date(2018, 1, 1),
        failure_date=date(2023, 1, 1),
        lifespan_months=60,
        founding_date_unknown=False,
        failure_date_unknown=False,
        facets=_facets(),
    )


def test_allowed_hosts_unions_fixed_with_synthesis_sources() -> None:
    s = _synthesis(
        candidate_id="cand-1",
        sources=["https://example.com/a", "https://blog.example.org/b"],
    )
    hosts = _allowed_hosts_for_candidate(s)
    assert "news.ycombinator.com" in hosts  # fixed
    assert "example.com" in hosts
    assert "blog.example.org" in hosts


def test_allowed_hosts_with_no_synthesis_sources_is_fixed_only() -> None:
    s = _synthesis(candidate_id="cand-1", sources=[])
    hosts = _allowed_hosts_for_candidate(s)
    assert hosts == frozenset({"news.ycombinator.com"})


def test_score_synthesis_treats_missing_body_as_vacuously_grounded() -> None:
    s = _synthesis(candidate_id="cand-1", sources=["https://news.ycombinator.com/x"])
    result = _score_synthesis(s, bodies_map={"cand-1": None})
    assert result["claims_grounded_in_body"] is True


def test_score_report_emits_baseline_shape() -> None:
    s1 = _synthesis(candidate_id="cand-1", sources=["https://news.ycombinator.com/x"])
    s2 = _synthesis(candidate_id="cand-2", sources=["https://news.ycombinator.com/y"])
    report = Report(
        input=InputContext(name="x", description="y"),
        candidates=[s1, s2],
        top_risks=None,
        pipeline_meta=PipelineMeta(
            K_retrieve=10,
            N_synthesize=2,
            cost_usd_total=0.0,
            latency_ms_total=0,
            budget_exceeded=False,
            trace_id=None,
            models={"facet": "x", "rerank": "y", "synthesize": "z"},
        ),
    )
    result = _score_report(report, bodies_map={"cand-1": None, "cand-2": None})
    assert result["candidates_count"] == 2
    assert set(result["assertions"].keys()) == {"cand-1", "cand-2"}
    for assertions in result["assertions"].values():
        assert set(assertions.keys()) == {
            "where_diverged_nonempty",
            "all_sources_in_allowed_domains",
            "lifespan_months_positive",
            "claims_grounded_in_body",
        }
```

- [x] **Step 5: Run scoring tests to verify they fail (target functions don't exist yet)**

Run: `uv run pytest tests/evals/test_runner_scoring.py -v`
Expected: every test fails with `ImportError` — `_allowed_hosts_for_candidate`, `_score_synthesis`, `_score_report` don't yet take the new shape (the existing ones take `eval_corpus`, not `bodies_map`).

### 1C — Wire the cassette runner

- [x] **Step 6: Replace `_run_deterministic` with `_run_cassettes`**

In `slopmortem/evals/runner.py`:

1. **Delete** these symbols (top to bottom of the module):
   - `_facets`, `_payload`, `_candidate`
   - `_facet_extract_payload`, `_rerank_payload`, `_synthesis_payload`
   - `_build_canned`
   - `_EvalCorpus`
   - `_no_op_sparse_encoder`
   - `_DETERMINISTIC_FACET_MODEL`, `_DETERMINISTIC_RERANK_MODEL`, `_DETERMINISTIC_SYNTH_MODEL`, `_DETERMINISTIC_EMBED_MODEL`
   - `_build_deterministic_config`
   - `_run_deterministic`
   - `_allowed_hosts_for_candidate` (rewritten in Step 7)
   - `_body_for_candidate` (rewritten in Step 7)
   - `_score_synthesis` (rewritten in Step 7)
   - `_score_report` (rewritten in Step 7)

2. **Add** at the top of the module (with the other `slopmortem.*` imports):

```python
from slopmortem.config import load_config
from slopmortem.evals.cassettes import (
    NoCannedEmbeddingError,
    load_row_fakes,
)
from slopmortem.evals.qdrant_setup import setup_ephemeral_qdrant
from slopmortem.llm.fake import NoCannedResponseError
from slopmortem.llm.openai_embeddings import EMBED_DIMS
```

3. **Add** the cassette-root constant (placed near the top, with the other module-level constants like `_FIXED_HOST_ALLOWLIST`):

```python
# Module-level so tests can monkeypatch it to point at a tmp tree without
# touching the committed fixtures.
_CASSETTE_ROOT: Path = Path("tests/fixtures/cassettes/evals")
```

4. **Add** `_run_cassettes` (replacing `_run_deterministic`):

```python
async def _run_cassettes(
    rows: list[InputContext],
    row_ids: list[str],
    scope_filter: str | None,
) -> dict[str, dict[str, object]]:
    """Run every row in cassette mode and return per-row scored results.

    Opens an ephemeral Qdrant collection seeded from
    ``tests/fixtures/corpus_fixture.jsonl``. For each row, loads the
    committed cassette dir under ``_CASSETTE_ROOT/<row_id>/``, runs
    ``pipeline.run_query`` with fakes pinned to that cassette, and pre-fetches
    the per-candidate body via ``corpus.get_post_mortem`` before scoring. The
    host allowlist is computed directly from ``Synthesis.sources``.

    Per-row failures (missing cassette dir, ``NoCannedResponseError``,
    ``NoCannedEmbeddingError``) log ``FAIL <row_id>: …`` and write
    ``candidates_count=0`` for that row. Run-level failures (missing fixture,
    ``CassetteFormatError``) print to stderr and exit 2.
    """
    cfg = load_config()
    fixture_path = Path("tests/fixtures/corpus_fixture.jsonl")
    if not fixture_path.exists():
        print(  # noqa: T201 — CLI surface
            f"missing {fixture_path}; run `just eval-record-corpus` first",
            file=sys.stderr,
        )
        sys.exit(2)
    dim = EMBED_DIMS[cfg.embed_model_id]

    budget = Budget(cap_usd=2.0)
    results: dict[str, dict[str, object]] = {}
    async with setup_ephemeral_qdrant(fixture_path, dim=dim) as corpus:
        for ctx, rid in zip(rows, row_ids, strict=True):
            if scope_filter is not None and rid != scope_filter:
                continue
            scope_dir = _CASSETTE_ROOT / rid
            if not scope_dir.exists() or not any(scope_dir.iterdir()):
                print(f"FAIL {rid}: no cassettes")  # noqa: T201 — CLI surface
                results[rid] = {"candidates_count": 0, "assertions": {}}
                continue

            fake_llm, fake_embed, cassette_sparse = load_row_fakes(scope_dir, cfg)

            try:
                report = await run_query(
                    ctx,
                    llm=fake_llm,
                    embedding_client=fake_embed,
                    corpus=corpus,
                    config=cfg,
                    budget=budget,
                    sparse_encoder=cassette_sparse,
                )
            except (NoCannedResponseError, NoCannedEmbeddingError) as exc:
                print(f"FAIL {rid}: cassette miss — {exc}")  # noqa: T201 — CLI surface
                results[rid] = {"candidates_count": 0, "assertions": {}}
                continue

            bodies_map: dict[str, str | None] = {}
            for s in report.candidates:
                try:
                    bodies_map[s.candidate_id] = await corpus.get_post_mortem(s.candidate_id)
                except (KeyError, FileNotFoundError):
                    bodies_map[s.candidate_id] = None
            results[rid] = _score_report(report, bodies_map=bodies_map)
    return results
```

The `Mapping` annotation hoist to `TYPE_CHECKING` already exists in the file. No new conditional imports are needed for `_run_cassettes`.

- [x] **Step 7: Rewrite the scoring helpers around `Synthesis.sources` + `bodies_map`**

In `slopmortem/evals/runner.py`, add these (replacing the deleted `_allowed_hosts_for_candidate`, `_body_for_candidate`, `_score_synthesis`, `_score_report`):

```python
def _allowed_hosts_for_candidate(s: Synthesis) -> set[str]:
    """Compute the host allowlist for ``all_sources_in_allowed_domains``.

    Unions the fixed allowlist with the candidate's own ``Synthesis.sources``
    (already populated from ``CandidatePayload.sources`` in
    ``Synthesis.from_llm``). Rebuilds the set fresh per call so it can't leak
    across candidates.
    """
    hosts: set[str] = set(_FIXED_HOST_ALLOWLIST)
    for url in s.sources:
        host = urlparse(url).hostname
        if host is not None:
            hosts.add(host)
    return hosts


def _score_synthesis(
    s: Synthesis,
    *,
    bodies_map: Mapping[str, str | None],
) -> dict[str, bool]:
    """Apply every assertion in :data:`_ASSERTION_NAMES` to *s*."""
    allowed = _allowed_hosts_for_candidate(s)
    body = bodies_map.get(s.candidate_id)
    return {
        "where_diverged_nonempty": where_diverged_nonempty(s),
        "all_sources_in_allowed_domains": all_sources_in_allowed_domains(s, allowed),
        "lifespan_months_positive": lifespan_months_positive(s),
        "claims_grounded_in_body": True if body is None else claims_grounded_in_body(s, body),
    }


def _score_report(
    report: Report,
    *,
    bodies_map: Mapping[str, str | None],
) -> dict[str, object]:
    """Return the per-row results-dict in the baseline shape."""
    assertions: dict[str, dict[str, bool]] = {}
    for s in report.candidates:
        assertions[s.candidate_id] = _score_synthesis(s, bodies_map=bodies_map)
    return {
        "candidates_count": len(report.candidates),
        "assertions": assertions,
    }
```

`claims_grounded_in_body` now actually runs in both modes. The `True if body is None` arm only fires when the corpus has no canonical body for that id.

- [x] **Step 8: Update `_run_live` to use the same scoring path**

Replace the `_run_live` body. Build `bodies_map` from the prod corpus the same way `_run_cassettes` does:

```python
async def _run_live(rows: list[InputContext], row_ids: list[str]) -> dict[str, dict[str, object]]:
    """Run every row through real production deps. May spend real money.

    Behavior change vs. the old deterministic-only baseline: ``claims_grounded_in_body``
    now actually runs against the post-mortem body. Previously it was vacuously
    True because ``_run_live`` passed no corpus to the scorer.
    """
    from slopmortem.cli import (  # noqa: PLC0415
        _build_deps,  # pyright: ignore[reportPrivateUsage]
    )
    from slopmortem.corpus.tools_impl import _set_corpus  # noqa: PLC0415

    cfg = load_config()
    llm, embedder, corpus, budget = _build_deps(cfg)
    _set_corpus(corpus)

    results: dict[str, dict[str, object]] = {}
    for ctx, rid in zip(rows, row_ids, strict=True):
        report = await run_query(
            ctx,
            llm=llm,
            embedding_client=embedder,
            corpus=corpus,
            config=cfg,
            budget=budget,
        )
        bodies_map: dict[str, str | None] = {}
        for s in report.candidates:
            try:
                bodies_map[s.candidate_id] = await corpus.get_post_mortem(s.candidate_id)
            except (KeyError, FileNotFoundError):
                bodies_map[s.candidate_id] = None
        results[rid] = _score_report(report, bodies_map=bodies_map)
    return results
```

The local `from slopmortem.config import load_config` that previously lived inside `_run_live` is removed in favor of the module-level import added in Step 6.

- [x] **Step 9: Add scope validation to `main` and rewire the dispatch**

In `main()` in `slopmortem/evals/runner.py`, add the scope check immediately after `row_ids = _verify_unique_row_ids(rows)`:

```python
    rows = _load_dataset(dataset_path)
    row_ids = _verify_unique_row_ids(rows)

    if scope is not None and scope not in row_ids:
        print(  # noqa: T201 — CLI surface
            f"--scope {scope!r} not in dataset; valid scopes: {sorted(row_ids)}",
            file=sys.stderr,
        )
        sys.exit(2)

    if live:
        results = anyio.run(_run_live, rows, row_ids)
    else:
        results = anyio.run(_run_cassettes, rows, row_ids, scope)
```

The `scope_filter` parameter on `_run_cassettes` is *positional* (no leading `*`), so `anyio.run(_run_cassettes, rows, row_ids, scope)` forwards it correctly.

`_run_record` already validates scope via its own check (`runner.py:704-712`); the gate above also catches `--scope notarow` invocations that fall through to replay/live.

- [x] **Step 10: Update the module docstring**

In `slopmortem/evals/runner.py`, replace the existing `Modes:` block (lines 7-19) and delete the `Live-mode limitation` section (lines 60-67).

New `Modes:` block:

```
Modes:
    DEFAULT (cassettes): FakeLLMClient + FakeEmbeddingClient backed by
        committed cassettes under tests/fixtures/cassettes/evals/<row_id>/,
        plus an ephemeral Qdrant collection seeded from
        tests/fixtures/corpus_fixture.jsonl. Requires a running Qdrant
        instance on localhost:6333. This is what `just eval` and CI run.
    --live: real production deps via slopmortem.cli._build_deps. Operator-
        invoked, out of CI scope. Costs real money.
    --record: re-record cassettes against the live API. Calls
        record_cassettes_for_inputs() with --max-cost-usd as the ceiling.
    --scope <row_id>: restrict record or replay to one row. Unknown scopes
        exit 2 with a usage error before any pipeline call.
    --write-baseline: write the current run's results to --baseline.
```

Delete the entire `Live-mode limitation` paragraph. Both modes now compute the per-candidate allowlist directly from `Synthesis.sources`, so the limitation no longer applies.

- [x] **Step 11: Update the argparse description**

In `_build_argparser()` (currently at `runner.py:621-630`), replace the description string:

```python
    description=(
        "Run a JSONL eval dataset through the synthesis pipeline and "
        "compare per-row assertion results against a recorded baseline. "
        "Default mode replays committed cassettes against an ephemeral Qdrant "
        "collection (FakeLLMClient + FakeEmbeddingClient + setup_ephemeral_qdrant); "
        "--live wires real production deps. CI runs cassette mode only."
    ),
```

### 1D — Verify

- [x] **Step 12: Run typecheck**

Run: `just typecheck`
Expected: PASS.

- [x] **Step 13: Run scoring unit tests (no Qdrant needed)**

Run: `uv run pytest tests/evals/test_runner_scoring.py -v`
Expected: PASS.

- [x] **Step 14: Run the runner-replay tests**

Run: `docker compose up -d qdrant && uv run pytest tests/evals/test_runner_replay.py -v`
Expected: every active test PASSES. Test 3 (`unknown_scope_is_fatal`) is the only one that runs without the marker; all others gate on Qdrant.

Note: the happy-path test (`test_runner_replay_passes_with_recorded_cassettes`) requires the committed cassette dir for `ledgermint` to contain *all* stages (facet, rerank, N synthesize, consolidate_risks, embeds). If the committed cassettes are still incomplete from prior crashed recording sessions, this test fails until Task 2 lands. That is expected — Task 2 re-records.

If the happy-path test fails at this stage with a `cassette miss` line, mark Step 14 as `- [x]` *only after* confirming the failure is due to incomplete committed cassettes (not a bug in the cassette wiring). Tests 2-6 (missing-dir, unknown-scope, llm-cassette-miss, malformed-cassette, embed-model-swap) must still pass — they don't depend on the committed cassettes being complete.

- [x] **Step 15: Run the requires_qdrant suite**

Run: `docker compose up -d qdrant && just test -m requires_qdrant`
Expected: PASS, modulo the same caveat for the happy-path test above.

- [x] **Step 16: Smoke-check `just eval`**

Run: `docker compose up -d qdrant && just eval`
Expected: the runner reaches the cassette-load path without import errors. Per-row exit code is 1 (regression) or 0 (no regression) depending on cassette completeness vs. the stale baseline. The test of merit at this stage: no `_EvalCorpus` AttributeError, no import-time failure, no `TypeError: takes 2 positional arguments but 3 were given`. The wiring is functional; Task 2 makes the run produce a correct baseline.

- [x] **Step 17: Lint**

Run: `just lint`
Expected: PASS.

---

## Task 2: OPERATOR — re-record eval cassettes and regenerate the baseline

This is a manual human-in-the-loop step. No subagent runs. Costs real money (~$2 per the existing `--max-cost-usd` default).

The current cassette dirs under `tests/fixtures/cassettes/evals/<row_id>/` are incomplete: each contains only `facet_extract`, ONE `synthesize`, and the embed pair. `pipeline.run_query` always invokes `llm_rerank` and `consolidate_risks`, and runs `synthesize` `N_synthesize` (=5) times. Without the full set, replay hits `NoCannedResponseError` and every row gets `candidates_count=0`.

This task re-records every scope dir against the live API, then regenerates `tests/evals/baseline.json` from the now-complete cassettes.

- [ ] **Step 1: Pre-flight check — confirm cassette dirs exist for every dataset row**

Run:
```bash
comm -23 \
  <(jq -r .name tests/evals/datasets/seed.jsonl | sort) \
  <(ls tests/fixtures/cassettes/evals/ | sort)
```
Expected: empty output. If any row name is reported, that row has no scope dir at all — flag and stop. Stale `kakikaki.36851...recording` is a leftover, not a real scope dir, and is removed in Task 5.

- [ ] **Step 2: Start Qdrant**

Run: `docker compose up -d qdrant`

- [ ] **Step 3: Re-record every scope (full eval-record run)**

Run:
```bash
uv run python -m slopmortem.evals.runner \
  --dataset tests/evals/datasets/seed.jsonl \
  --baseline tests/evals/baseline.json \
  --record \
  --max-cost-usd 5.0
```
Expected: process exits 0. Each `tests/fixtures/cassettes/evals/<row_id>/` directory now has `facet_extract__*.json`, `llm_rerank__*.json`, multiple `synthesize__*.json` (one per top-N candidate), `consolidate_risks__*.json`, and the embed pair. Verify with `ls tests/fixtures/cassettes/evals/ledgermint/` — expect ≥ 8 files.

- [ ] **Step 4: Spot-check one re-recorded scope**

Pick one row (e.g. `ledgermint`). Confirm:
- `synthesize__*.json` count equals `cfg.N_synthesize` (5 by default) modulo budget truncation.
- `llm_rerank__*.json` is present.
- `consolidate_risks__*.json` is present.
- One `synthesize` cassette opens to JSON whose `response.text` parses as the `LLMSynthesis` shape (look for `where_diverged`, `failure_causes`, `lessons_for_input`).

If any expected file is missing for any row, identify which stage was skipped (often a budget cutoff) and re-record the affected scope alone:
```bash
uv run python -m slopmortem.evals.runner \
  --dataset tests/evals/datasets/seed.jsonl \
  --baseline tests/evals/baseline.json \
  --record --scope <row_id> --max-cost-usd 1.0
```

- [ ] **Step 5: Delete the stale baseline**

Run: `rm tests/evals/baseline.json`

- [ ] **Step 6: Write the new baseline from the now-complete cassettes**

Run:
```bash
uv run python -m slopmortem.evals.runner \
  --dataset tests/evals/datasets/seed.jsonl \
  --baseline tests/evals/baseline.json \
  --write-baseline
```
Expected: process exits 0, `tests/evals/baseline.json` gets created. The output ends with `wrote baseline to tests/evals/baseline.json`.

- [ ] **Step 7: Visually review the new baseline**

Open `tests/evals/baseline.json` and check:

- Every row from `tests/evals/datasets/seed.jsonl` (10 rows: ledgermint, vitalcue, …) is present.
- `candidates_count > 0` for every row. A `0` means the cassettes are still incomplete for that row — return to Step 4.
- No candidate ID equals `"acme"`. Real candidate IDs match what's in the corpus fixture.
- Every assertion in every row is `true`. If any assertion is `false`:
  - `where_diverged_nonempty=false`: the recorded synthesize response had an empty `where_diverged` — re-record (LLM may have produced a degenerate output once).
  - `all_sources_in_allowed_domains=false`: the recorded synthesize response cited an off-domain URL. Investigate before re-recording — may indicate a real prompt drift.
  - `lifespan_months_positive=false`: payload-level founding/failure dates are off; check the corpus fixture.
  - `claims_grounded_in_body=false`: the body lookup found a real body and the assertion failed against it. Investigate; do NOT lower the bar.

Do not lower assertion bars to make a row pass.

If the baseline looks right: commit it along with the regenerated cassettes. If anything's off: stop and investigate before running later tasks.

- [ ] **Step 8: Confirm `just eval` is green against the new baseline**

Run: `just eval`
Expected: every row prints `PASS`, no `REGRESSION` lines, exit 0.

- [ ] **Step 9: Re-run the runner-replay happy-path test**

Run: `docker compose up -d qdrant && uv run pytest tests/evals/test_runner_replay.py::test_runner_replay_passes_with_recorded_cassettes -v`
Expected: PASS. (This is the one Task 1 left potentially red because the committed cassettes were incomplete.)

---

## Task 3: OPERATOR — record cassettes for the e2e test

This is a manual human-in-the-loop step. No subagent runs. Costs real money (a few cents).

`tests/test_pipeline_e2e.py::test_full_pipeline_with_fake_clients` migrates to cassette-backed mode in Task 4. That migration needs a recorded cassette dir at `tests/fixtures/cassettes/e2e/test_full_pipeline_with_fake_clients/`.

- [ ] **Step 1: Add a `record-e2e` recipe to the justfile**

Append to `justfile`:

```make
# Record cassettes for the e2e pipeline test. One-shot; reruns are idempotent.
record-e2e:
    docker compose up -d qdrant
    uv run python -m slopmortem.evals.recording_helper \
        --inputs-jsonl <(echo '{"name":"test_full_pipeline_with_fake_clients","description":"A B2B fintech for SMB invoicing"}') \
        --output-dir tests/fixtures/cassettes/e2e \
        --max-cost-usd 1.0
```

If `slopmortem.evals.recording_helper` does not currently expose a CLI entry point with `--inputs-jsonl` / `--output-dir` / `--max-cost-usd`, prefer this minimal Python invocation instead (avoids growing a new top-level script):

```make
record-e2e:
    docker compose up -d qdrant
    uv run python -c '\
import asyncio; \
from pathlib import Path; \
from slopmortem.config import load_config; \
from slopmortem.evals.recording_helper import record_cassettes_for_inputs; \
from slopmortem.models import InputContext; \
asyncio.run(record_cassettes_for_inputs( \
    inputs=[InputContext(name="test_full_pipeline_with_fake_clients", \
                         description="A B2B fintech for SMB invoicing")], \
    output_dir=Path("tests/fixtures/cassettes/e2e"), \
    corpus_fixture_path=Path("tests/fixtures/corpus_fixture.jsonl"), \
    config=load_config(), \
    max_cost_usd=1.0))'
```

The recipe is the canonical re-record entry point — keep it in the justfile rather than adding a new top-level script. The `name` and `description` strings in the recipe match the constants used in Task 4's test body (single source of truth).

- [ ] **Step 2: Create the parent directory**

Run: `mkdir -p tests/fixtures/cassettes/e2e`

- [ ] **Step 3: Run the recipe**

Run: `just record-e2e`

The recorder names the scope dir via `_row_id(ctx)` — because `ctx.name == "test_full_pipeline_with_fake_clients"`, the dir is `tests/fixtures/cassettes/e2e/test_full_pipeline_with_fake_clients/`.

Expected: that directory is populated with `facet_extract__*.json`, multiple `synthesize__*.json`, `llm_rerank__*.json`, `consolidate_risks__*.json`, and a set of `embed__*.json` files (one dense per text + one sparse per text).

- [ ] **Step 4: Verify the cassette dir shape**

Run: `ls tests/fixtures/cassettes/e2e/test_full_pipeline_with_fake_clients/`

Expected: at least one file each matching `facet_extract__*.json`, `synthesize__*.json`, `llm_rerank__*.json`, `consolidate_risks__*.json`, `embed__*.json`. Spot-check a synthesize cassette and confirm `response.text` parses as the expected `LLMSynthesis` JSON shape.

- [ ] **Step 5: Commit the cassette dir + the justfile recipe**

Add `tests/fixtures/cassettes/e2e/test_full_pipeline_with_fake_clients/` and the `justfile` change to git and commit.

---

## Task 4: Migrate `test_full_pipeline_with_fake_clients` to cassettes

**Files:**
- Modify: `tests/test_pipeline_e2e.py` (only `test_full_pipeline_with_fake_clients` and add module-scope `_CountingCorpus` + e2e cassette name/description constants)

Do NOT touch `test_run_query_forwards_sparse_encoder`, `test_run_query_marks_budget_exceeded_on_llm_overspend`, `test_run_query_records_budget_exceeded`, `test_ctrl_c_cancels_in_flight`, or any other test in the file.

Do NOT remove `_FakeCorpus`, `_build_canned`, `_no_op_sparse_encoder`, or any of the `_payload`/`_candidate`/`_facets` helpers from the file — the other tests still use them.

- [ ] **Step 1: Read the original test to enumerate every assertion**

Open `tests/test_pipeline_e2e.py`, find `test_full_pipeline_with_fake_clients` (around line 347). Count and categorize the assertions:

Group A (port verbatim — no shape change):
1. `report.input == ctx`
2. `isinstance(report.candidates, list)`
3. `0 < len(report.candidates) <= cfg.N_synthesize`
4. `all(isinstance(s, Synthesis) for s in report.candidates)`
5. `meta.K_retrieve == cfg.K_retrieve`
6. `meta.N_synthesize == cfg.N_synthesize`
7. `meta.cost_usd_total == budget.spent_usd`
8. `meta.latency_ms_total >= 0`
9. `meta.budget_exceeded is False`
10. `meta.trace_id is None`
11. `set(meta.models.keys()) == {"facet", "rerank", "synthesize"}`
12. `meta.models["facet"] == cfg.model_facet` (and `rerank`, `synthesize`)
13. `phases_started == {FACET_EXTRACT, RETRIEVE, RERANK, SYNTHESIZE}`
14. `synth_advances == cfg.N_synthesize`

Group B (top_risks — exact shape locked in Step 4 after observing the cassette):
15. `isinstance(report.top_risks, TopRisks)`
16. exact `len(report.top_risks.risks)` == observed count
17. exact `raised_by` == observed list
18. exact `severity` == observed value

Group C (corpus introspection — replaced by `_CountingCorpus`):
19. `len(counting_corpus.queries) == 1`
20. `q["k_retrieve"] == cfg.K_retrieve`
21. `q["strict_deaths"] == cfg.strict_deaths`

Total: 21 assertions. Step 4 enforces that the count stays at 21 after the second pass.

- [ ] **Step 2: Add module-scope `_CountingCorpus` and cassette constants**

In `tests/test_pipeline_e2e.py`, add at module scope (next to `_FakeCorpus` at line 249):

```python
# Pinned strings shared between the e2e test and `just record-e2e` so a
# typo in either place would loudly mismatch cassette keys instead of
# silently drifting.
_E2E_CASSETTE_NAME = "test_full_pipeline_with_fake_clients"
_E2E_CASSETTE_DESCRIPTION = "A B2B fintech for SMB invoicing"


class _CountingCorpus:
    """Wraps a real Corpus and records each ``query()`` invocation.

    Explicit forwarders for every Corpus Protocol method — no ``__getattr__``
    fallback, no ``# type: ignore``. Keeps basedpyright strict and matches the
    pattern used by ``_FakeCorpus`` elsewhere in this file.
    """

    def __init__(self, inner: Corpus) -> None:
        self._inner = inner
        self.queries: list[dict[str, object]] = []

    async def query(  # noqa: PLR0913 — mirrors Corpus Protocol kwargs-only signature
        self,
        *,
        dense: list[float],
        sparse: dict[int, float],
        facets: Facets,
        cutoff_iso: str | None,
        strict_deaths: bool,
        k_retrieve: int,
    ) -> list[Candidate]:
        self.queries.append(
            {
                "k_retrieve": k_retrieve,
                "strict_deaths": strict_deaths,
            }
        )
        return await self._inner.query(
            dense=dense,
            sparse=sparse,
            facets=facets,
            cutoff_iso=cutoff_iso,
            strict_deaths=strict_deaths,
            k_retrieve=k_retrieve,
        )

    async def get_post_mortem(self, canonical_id: str) -> str:
        return await self._inner.get_post_mortem(canonical_id)

    async def search_corpus(
        self,
        *,
        query_text: str,
        k: int,
    ) -> list[Candidate]:
        return await self._inner.search_corpus(query_text=query_text, k=k)
```

Add `Corpus` to the imports at the top of the file:

```python
from slopmortem.corpus.store import Corpus
```

- [ ] **Step 3: Replace the test body**

Replace the body of `test_full_pipeline_with_fake_clients` with:

```python
@pytest.mark.requires_qdrant
async def test_full_pipeline_with_fake_clients() -> None:
    """End-to-end run against committed cassettes + ephemeral Qdrant."""
    from slopmortem.config import load_config
    from slopmortem.evals.cassettes import load_row_fakes
    from slopmortem.evals.qdrant_setup import setup_ephemeral_qdrant
    from slopmortem.llm.openai_embeddings import EMBED_DIMS

    scope = Path("tests/fixtures/cassettes/e2e") / _E2E_CASSETTE_NAME
    fixture = Path("tests/fixtures/corpus_fixture.jsonl")
    cfg = load_config()
    ctx = InputContext(name=_E2E_CASSETTE_NAME, description=_E2E_CASSETTE_DESCRIPTION)

    fake_llm, fake_embed, cassette_sparse = load_row_fakes(scope, cfg)
    budget = Budget(cap_usd=2.0)
    progress = _RecordingQueryProgress()

    async with setup_ephemeral_qdrant(fixture, dim=EMBED_DIMS[cfg.embed_model_id]) as qdrant_corpus:
        counting_corpus = _CountingCorpus(qdrant_corpus)
        report = await run_query(
            ctx,
            llm=fake_llm,
            embedding_client=fake_embed,
            corpus=counting_corpus,
            config=cfg,
            budget=budget,
            sparse_encoder=cassette_sparse,
            progress=progress,
        )

    # Group A — verbatim contract assertions.
    assert report.input == ctx
    assert isinstance(report.candidates, list)
    assert 0 < len(report.candidates) <= cfg.N_synthesize
    assert all(isinstance(s, Synthesis) for s in report.candidates)
    meta = report.pipeline_meta
    assert meta.K_retrieve == cfg.K_retrieve
    assert meta.N_synthesize == cfg.N_synthesize
    assert meta.cost_usd_total == budget.spent_usd
    assert meta.latency_ms_total >= 0
    assert meta.budget_exceeded is False
    assert meta.trace_id is None
    assert set(meta.models.keys()) == {"facet", "rerank", "synthesize"}
    assert meta.models["facet"] == cfg.model_facet
    assert meta.models["rerank"] == cfg.model_rerank
    assert meta.models["synthesize"] == cfg.model_synthesize

    phases_started = {evt[1] for evt in progress.events if evt[0] == "start"}
    assert phases_started == {
        QueryPhase.FACET_EXTRACT,
        QueryPhase.RETRIEVE,
        QueryPhase.RERANK,
        QueryPhase.SYNTHESIZE,
    }
    synth_advances = sum(
        cast("int", evt[2])
        for evt in progress.events
        if evt[0] == "advance" and evt[1] == QueryPhase.SYNTHESIZE
    )
    assert synth_advances == len(report.candidates)

    # Group B — top_risks shape (Step 4 tightens these to exact values).
    assert isinstance(report.top_risks, TopRisks)
    assert len(report.top_risks.risks) >= 1  # tightened in Step 4

    # Group C — corpus query introspection.
    assert len(counting_corpus.queries) == 1
    q = counting_corpus.queries[0]
    assert q["k_retrieve"] == cfg.K_retrieve
    assert q["strict_deaths"] == cfg.strict_deaths
```

- [ ] **Step 4: Tighten Group B assertions against the recorded cassette**

This is a *separate* checkbox so it cannot be silently skipped.

Open `tests/fixtures/cassettes/e2e/test_full_pipeline_with_fake_clients/consolidate_risks__*.json`. Read `response.text` and parse the JSON. Identify:

- Exact count of risks: `len(parsed["risks"])` → e.g. 1.
- For each risk, the `severity` value (`"high" | "medium" | "low"`) and `raised_by` list (candidate IDs).

Replace the `assert len(report.top_risks.risks) >= 1` placeholder with:

```python
    assert len(report.top_risks.risks) == <observed_count>
    assert report.top_risks.risks[0].severity == "<observed_severity>"
    assert report.top_risks.risks[0].raised_by == [<observed_raised_by_list>]
```

Add additional asserts if the cassette commits multiple risks. Aim for total assertion count to land back at 21 (or higher if the cassette produces multiple risks).

If the cassette is unstable across re-records (e.g. an LLM produces a different severity on each run), keep the floor `>= 1` but document why with a one-line comment.

- [ ] **Step 5: Run the migrated test**

Run: `docker compose up -d qdrant && uv run pytest tests/test_pipeline_e2e.py::test_full_pipeline_with_fake_clients -v`
Expected: PASS.

- [ ] **Step 6: Run the rest of `test_pipeline_e2e.py`**

Run: `uv run pytest tests/test_pipeline_e2e.py -v`
Expected: every test in the file passes.

- [ ] **Step 7: Typecheck + lint**

Run: `just typecheck && just lint`
Expected: PASS.

- [ ] **Step 8: Smoke-check `just eval`**

Run: `docker compose up -d qdrant && just eval`
Expected: exit 0, no regressions.

---

## Task 5: Cleanup

**Files:**
- Delete: `tests/fixtures/cassettes/evals/kakikaki.36851.34fdf19c5a3e4d41bdf129cd1208dcd5.recording/`
- Modify: `slopmortem/evals/runner.py` (final read-through of the module docstring, the `--live` `--help` text, and the `_FIXED_HOST_ALLOWLIST` comment for accuracy)

Do NOT touch any other file.

- [ ] **Step 1: Verify nothing references the stale recording dir**

Run: `git grep "kakikaki.36851" || echo "no references"`
Expected: `no references`. If anything references the stale path, stop and investigate.

- [ ] **Step 2: Delete the stale recording dir**

Run: `rm -rf tests/fixtures/cassettes/evals/kakikaki.36851.34fdf19c5a3e4d41bdf129cd1208dcd5.recording`

The sibling `tests/fixtures/cassettes/evals/kakikaki/` (the live cassette dir for the `kakikaki` row) stays.

- [ ] **Step 3: Final read-through of `slopmortem/evals/runner.py`**

Open `slopmortem/evals/runner.py`. Read top to bottom and check:

- The module docstring `Modes:` block reflects cassette-default behavior + Qdrant requirement (Task 1 should have already fixed this).
- No reference to `_run_deterministic`, `_EvalCorpus`, `_no_op_sparse_encoder`, `_DETERMINISTIC_*_MODEL`, `_build_deterministic_config`, `_build_canned`, or `_synthesis_payload` remains.
- The `_FIXED_HOST_ALLOWLIST` comment still accurately describes its role (now consulted by both modes via `_allowed_hosts_for_candidate`, not just live).
- The `--live` argparse `help=` string reads correctly.

Make any minor prose tweaks needed for accuracy. No code changes.

- [ ] **Step 4: Run the full test suite**

Run: `just test`
Expected: PASS.

- [ ] **Step 5: Run typecheck**

Run: `just typecheck`
Expected: PASS.

- [ ] **Step 6: Run lint**

Run: `just lint`
Expected: PASS.

- [ ] **Step 7: Confirm `git status` is clean except for intentional changes**

Run: `git status`
Expected: only the deletion of the stale `kakikaki.36851.…recording/` dir and (optionally) minor prose edits to `slopmortem/evals/runner.py`. No surprise modifications.

- [ ] **Step 8: Final smoke-check `just eval`**

Run: `docker compose up -d qdrant && just eval`
Expected: every row prints `PASS`, exit 0.
