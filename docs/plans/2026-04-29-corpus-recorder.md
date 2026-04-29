# Plan: implement `corpus_recorder`

Date: 2026-04-29
Owner: parent (Claude Code session) → single subagent
Parent spec: `docs/specs/2026-04-29-eval-cassettes-design.md` (Task 5 in
`docs/plans/2026-04-29-eval-cassettes.md` lines 2337–2421)

## Goal

Replace the stub at `slopmortem/evals/corpus_recorder.py` with a working
operator-only CLI that builds `tests/fixtures/corpus_fixture.jsonl` from
`tests/fixtures/corpus_fixture_inputs.yml`. The recorder runs the real ingest
pipeline against a throwaway Qdrant collection, scrolls the result out via
`dump_collection_to_jsonl`, and atomically swaps it into place.

## Why now

Cassette recording (`just eval-record`) is gated on `corpus_fixture.jsonl`
existing — `slopmortem/evals/runner.py:719-722` hard-fails if the file is
missing. The corpus recorder is the only path to producing that file. Until it
exists, neither `just eval` nor `just eval-record` can run.

Ingest itself is unblocked and ready independently.

## Execution Strategy

**Parallel subagents** — single task, one agent. Reason: the scope is one new
file (`slopmortem/evals/corpus_recorder.py`) with no cross-cutting concerns and
no inter-task communication. The persistent-team coordination overhead of
`/team-feature` would not pay off. A single subagent runs to completion under
the parent's two-stage review.

## Agent Assignments

| Task | Agent type | Language/domain |
|------|------------|-----------------|
| 1. Implement `corpus_recorder.py` | `python-development:python-pro` | Python 3.14 + asyncio |

## File ownership

**MODIFY (only file the agent may write to):**
- `slopmortem/evals/corpus_recorder.py`

**READ-ONLY (the agent must study these to mirror existing patterns; do not edit):**
- `slopmortem/cli.py` — `_run_ingest()` at line 158-252; canonical pattern for
  building ingest dependencies. Mirror it.
- `slopmortem/ingest.py:651` — `ingest()` keyword-only signature.
- `slopmortem/corpus/sources/curated.py:61-129` — `CuratedSource` constructor
  and YAML schema (`startup_name`, `url`).
- `slopmortem/evals/recording.py` — `RecordingLLMClient` constructor and
  cost-cap behaviour.
- `slopmortem/evals/corpus_fixture.py` — `dump_collection_to_jsonl` output
  shape; the recorder is its only producer.
- `slopmortem/corpus/qdrant_store.py` — `QdrantCorpus` constructor; the
  collection-name argument is what the recorder must own end-to-end.
- `slopmortem/corpus/merge.py` — `MergeJournal` constructor; takes a sqlite
  path.
- `slopmortem/llm/openrouter.py` — `OpenRouterClient` constructor.
- `slopmortem/llm/fastembed_client.py` — `FastEmbedClient` constructor.
- `slopmortem/budget.py` — `Budget` constructor and `max_cost_usd_per_ingest`
  parameter.
- `slopmortem/config.py` — `load_config()` and the `Config` shape.

## Out of scope (do not do, even if it looks tempting)

- No new test files. The spec marks this operator-manual; the end-to-end test
  is gated on `requires_qdrant` + `RUN_LIVE` and is the operator's
  responsibility.
- No edits to any file outside `slopmortem/evals/corpus_recorder.py`. In
  particular: no aliasing `name` → `startup_name` in `CuratedSource`, no new
  helpers in other modules.
- No `--gc-orphans` flag. Deferred follow-up.
- No baseline updates, no LFS tracking changes. Operator does that after a
  successful run.
- No `git add`, no `git commit`, no `git push`. The parent owns commit
  authorship.
- No new dependencies. Everything required is already in `pyproject.toml`.

## Architecture

The module is one CLI entry plus one async orchestrator.

```
main(argv: list[str] | None = None) -> None              # sync, ~25 lines
  argparse: --inputs Path (required)
            --out Path (required)
            --max-cost-usd float (default 1.5)
            --qdrant-url str (default reads from config)
  if not os.environ.get("RUN_LIVE"): print message; sys.exit(2)
  asyncio.run(_record(...))

_record(inputs_path, out_path, max_cost_usd, qdrant_url) -> None  # async, ~100 lines
  load config
  read seed-input YAML; translate to curated YAML in a TemporaryDirectory
  build a tempdir for post_mortems_root
  build MergeJournal pointing at sqlite inside the tempdir
  open AsyncQdrantClient
  pick collection name: f"slopmortem_corpus_record_{os.getpid()}_{uuid.uuid4().hex}"
  build QdrantCorpus on that collection
  build OpenRouterClient → wrap in RecordingLLMClient(max_cost_usd=...)
  build FastEmbedClient (or whichever EmbeddingClient cli._run_ingest uses)
  build Budget(max_cost_usd_per_ingest=max_cost_usd)
  build Binoculars slop classifier
  empty enrichers list
  try:
    await ingest(
      sources=[CuratedSource(translated_yaml_path)],
      enrichers=[],
      journal=journal,
      corpus=corpus,
      llm=recording_llm,
      embed_client=embed,
      budget=budget,
      slop_classifier=slop,
      config=config,
      post_mortems_root=tempdir / "post_mortems",
    )
    out_tmp = out_path.with_suffix(out_path.suffix + ".recording")
    await dump_collection_to_jsonl(qclient, collection_name, out_tmp)
    os.replace(out_tmp, out_path)
    print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")
  finally:
    with contextlib.suppress(Exception):
      await qclient.delete_collection(collection_name)
    await qclient.close()
```

### YAML translation

Seed schema: `[{name: str, description: str, url: str}, ...]`
Curated schema (consumed by `CuratedSource`): `[{startup_name: str, url: str, ...}, ...]`

The translator maps `name` → `startup_name` and drops `description` (curated
doesn't use it). It writes the translated YAML inside the same
`TemporaryDirectory` that backs `post_mortems_root`, so cleanup is automatic.

## Pros / cons of the chosen design

**Reuse `CuratedSource` via in-memory YAML translation (chosen)**
- Pros: zero prod-code changes; exercises the same fetch / robots / throttle /
  trafilatura path that production uses; ~10 lines of translation code.
- Cons: writes a tempfile YAML that exists only inside `TemporaryDirectory`.
  Negligible.

**Reject — write a tiny `SeedInputSource` class**
- Pros: avoids the tempfile.
- Cons: duplicates fetch / robots / throttle / extract logic that's already in
  `CuratedSource`. The duplicate would drift.

**Reject — alias `name` → `startup_name` in `CuratedSource`**
- Pros: even less code.
- Cons: pollutes prod code with a test-only feature. Violates ownership.

## Steps the agent must follow, in order

1. Read `slopmortem/cli.py:158-252` (`_run_ingest`) to learn the canonical
   dependency-construction pattern. Copy what it does for the prod-mode path.
   Do not import `_run_ingest` itself — extract the pattern.
2. Read `slopmortem/ingest.py:651` for the `ingest()` kwarg surface.
3. Read `slopmortem/corpus/sources/curated.py` for the YAML schema the
   translation step has to produce.
4. Implement `_translate_seed_yaml(src: Path, dst: Path) -> None` — pure I/O,
   no async, ~10 lines. Validate that every input row has `name` and `url` as
   strings; raise `ValueError` with a helpful message on the first bad row.
5. Implement `_record(inputs_path, out_path, max_cost_usd, qdrant_url)`. Wrap
   the entire dependency-construction-and-ingest body in a single `async with
   contextlib.AsyncExitStack() as stack` so client + journal + tempdir close
   together on any exit path. The throwaway-collection drop must remain in a
   plain `try/finally` because it's stateful, not a context manager.
6. Replace the stub `main(...)` with `argparse` + `RUN_LIVE` gate +
   `asyncio.run(_record(...))`. Keep the existing exit-2 message format from
   the stub: `"eval-record-corpus requires RUN_LIVE=1 (live API spend)"`.
7. Run `just lint` until clean. Fix issues with `just format` only if they
   are pure formatting; logic-affecting lint findings get hand-fixed.
8. Run `just typecheck` until clean. The codebase uses `basedpyright` strict;
   the SDK-boundary `# pyright: ignore` pattern in
   `slopmortem/evals/corpus_fixture.py:1` is the model to follow if you hit
   the same Qdrant SDK weak-typing wall.
9. Do NOT run `just test` — no new tests are landing in this commit. The
   existing `tests/evals/test_corpus_fixture.py` still passes (unchanged).
10. Hand control back to the parent. Do not commit, do not stage, do not run
    the recorder live.

## Validation (operator runs after the agent finishes)

The agent does not run any of these. The user (parent) does, after both
reviews pass.

```bash
# preconditions, one-time
git lfs install
docker compose up -d qdrant
slopmortem embed-prefetch     # ~700 MB, idempotent

# smoke run with a small ceiling against a throwaway out-path
RUN_LIVE=1 uv run python -m slopmortem.evals.corpus_recorder \
  --inputs tests/fixtures/corpus_fixture_inputs.yml \
  --out /tmp/corpus_fixture_smoke.jsonl \
  --max-cost-usd 0.50

# expected: ~30 lines, file sha-stable, no orphan collection in Qdrant
wc -l /tmp/corpus_fixture_smoke.jsonl
curl -s localhost:6333/collections | jq '.result.collections[].name' | grep slopmortem_corpus_record_ || echo "no orphans"
```

If the smoke run looks right, re-run with the canonical out-path:

```bash
RUN_LIVE=1 uv run python -m slopmortem.evals.corpus_recorder \
  --inputs tests/fixtures/corpus_fixture_inputs.yml \
  --out tests/fixtures/corpus_fixture.jsonl \
  --max-cost-usd 1.50
```

Then the operator continues with Task 5 of the parent plan: `just eval-record`,
then commit fixtures + cassettes + baseline.

## Reviews (parent runs these before handing back to user)

1. **Spec-compliance review** — `feature-dev:code-reviewer`. Verify:
   - Only `slopmortem/evals/corpus_recorder.py` changed.
   - File contents match the architecture sketch above.
   - No scope creep (no new tests, no edits to other modules, no extra deps).
   - `RUN_LIVE` gate present with the exact message format.
   - Throwaway collection drop in `finally`, not contingent on success.
   - Atomic swap via `os.replace` of a `.recording` temp file.
2. **Code-quality review** — same agent or `git-pr-workflows:code-reviewer`.
   Verify:
   - Type annotations are strict-mode-clean (no untyped `Any` outside the
     established SDK-boundary pattern).
   - `# noqa` and `# pyright: ignore` comments carry rationale.
   - Function bodies stay under the existing pylint budgets.
   - No bare `except:`. The post-`finally` `delete_collection` uses
     `contextlib.suppress(Exception)` with a comment explaining why.

If either review surfaces critical or important issues, the implementer fixes
and the reviewer re-reviews until clean. Minor issues are noted, not blocking.

## Done definition

- `slopmortem/evals/corpus_recorder.py` matches the architecture above.
- `just lint` and `just typecheck` are green.
- Both reviews report no critical or important issues.
- Parent has not committed anything. The user (operator) commits and runs the
  live recorder themselves.
