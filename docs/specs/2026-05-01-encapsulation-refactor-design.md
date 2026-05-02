# Encapsulation Refactor — Design

Status: design, awaiting operator approval. Revised 2026-05-02 to reflect
out-of-band fix of the `evals→llm` leak (T1.0 now verification only) and to
correct the `merge_text` / `_db` / `_throttle` leak inventory.

Staged refactor that turns the existing aspirational `__init__.py` façades into
load-bearing boundaries, splits the two oversized top-level files into focused
sub-packages, and finishes with a visual `_internal/` convention. Five PRs,
each independently shippable.

## Problem

File-level encapsulation in this repo is decent — the heavy modules
(`corpus/entity_resolution.py` 668 LOC / 2 public defs, `corpus/merge.py`
454 / 2, `llm/openrouter.py` 359 / 4) keep their helpers properly underscored
and expose a tight class surface.

Package-level encapsulation is not enforced. The `__init__.py` façades exist
but callers freely reach past them. Concrete leaks observable today via
`grep -rn "^from slopmortem\." slopmortem/`:

- `from slopmortem.corpus.paths import safe_path` — used in 4 sites despite
  `corpus/__init__.py` re-exporting nothing of the sort.
- `from slopmortem.corpus.extract import extract_clean` — same pattern, 4 sites.
- `from slopmortem.corpus.merge_text import Section, combined_hash, combined_text`
  — `slopmortem/ingest.py:59` and `tests/corpus/test_merge_deterministic.py:7`.
  Two outside callers; T1.7's earlier classification of `merge_text` as
  rename-safe was wrong (see T1.7 note below).
- `from slopmortem.corpus.sources._throttle import …` — only one outside
  caller remaining: `tests/sources/test_robots_and_throttle.py:18`. The
  three production importers (`corpus/sources/wayback.py`,
  `corpus/sources/curated.py`, `corpus/sources/hn_algolia.py`) are siblings
  inside `corpus/sources/` and are not violations.
- `from slopmortem.llm.openrouter import OpenRouterClient` — `llm/__init__.py`
  only re-exports embedding clients, so the most-used class in the package
  bypasses the façade entirely. Three external sites today: `cli.py`,
  `evals/corpus_recorder.py`, `evals/recording_helper.py`.
- `from slopmortem.llm.embedding_factory import make_embedder` — new factory
  shared by `cli.py`, `evals/corpus_recorder.py`, and `evals/recording_helper.py`;
  not in the `llm/__init__.py` `__all__`.
- **Forbidden-direction violation resolved out-of-band.** The original
  `slopmortem/llm/fake_embeddings.py:9` import of `NoCannedEmbeddingError`
  from `slopmortem.evals.cassettes` was fixed in commit `f6557ac`
  (2026-05-02) by moving the symbol — and the rest of the cassettes module
  it lives in — to `slopmortem/llm/cassettes.py`. T1.0 below is now a
  verification step rather than a relocation.

`from slopmortem.corpus._db import connect` is **not** a current leak: the
two importers (`corpus/merge.py:30`, `corpus/entity_resolution.py:50`) are
both inside the `corpus` package. Listed here only because earlier drafts
of this design called it out.

Two top-level files have grown past the size where one-job-per-file holds:

- `slopmortem/cli.py` — 853 LOC, four typer subcommands plus two CLI-internal
  `RichPhaseProgress` subclasses in one file. (Down from 1109 LOC after the
  cassettes branch extracted `slopmortem/cli_progress.py` as a 239-LOC shared
  helper used by both `cli.py` and the eval recorders — see PR 3 notes for
  the implication: `cli_progress.py` stays at the top level, it cannot live
  under the future `cli/` package because `evals/` depends on it.)
- `slopmortem/ingest.py` — 1149 LOC, holds the warm-cache pattern, fan-out,
  journal write ordering, and slop-gate routing in a single module. The
  load-bearing invariants documented in `CLAUDE.md` ("mark_complete only after
  both writes succeed", "first entry alone, then fan out") have no dedicated
  home.

Without an enforcement layer, fixing this once doesn't stick — the next
refactor pass quietly re-introduces deep imports and the façades drift back
to "aspirational."

## Goals

- Every cross-package import in `slopmortem/*` goes through a package
  `__init__.py`. CI fails on bypasses.
- `ingest.py` and `cli.py` become packages whose internal files each have one
  responsibility, named so the load-bearing invariants are findable.
- Internal-only modules are visually obvious — first via `_`-prefixed names
  (PR 1), then via a sibling `_internal/` directory (PR 4).
- No behavior change. Cassette suite + smoke recipe pass at every checkpoint.

## Non-goals

- Rewriting any algorithm. This is purely a structural refactor.
- Touching `evals/`, `tracing/`, or the small utility modules
  (`config`, `models`, `budget`, `http`, `render`, `concurrency`, `errors`,
  `_time`) — they're either already correct or too small to warrant changes.
- Replacing typer, the LLM client, or any third-party dependency.
- Changing the public CLI surface. `just --list` of subcommands stays
  byte-identical.

## Decision summary

Selected the staged A → B → C transition presented during brainstorming.
Alternatives considered:

**Approach A only (façade hygiene + lint).** Pros: ~1 day of work, near-zero
behavior risk, immediate enforcement. Cons: leaves the two oversized files
untouched, so the actual maintenance pain in `ingest.py` and `cli.py` stays.

**Approach B only (skip the lint step, just split the big files).** Pros:
fixes the most visible smell first. Cons: without lint enforcement, the new
package boundaries leak immediately; we end up doing A anyway in a few months.

**Approach C only (jump straight to `_internal/` everywhere).** Pros: best
end-state visual signal. Cons: largest single git-history rewrite, biggest
review surface, and skips the cheap A win that would have caught regressions
during B and C.

**Why staged A → B → C:** each PR is independently valuable. A makes the
existing structure honest. B addresses the two files that are doing too many
jobs. C is optional polish — we may stop after PR 3 if the underscore
convention turns out to be enough. PRs 0–3 are firmly planned; PR 4 is
greenlit but reassessed after PR 3 ships.

## Work breakdown

### PR 0 — Tooling prep (~half a day)

| ID | Task | Files |
|---|---|---|
| T0.1 | Add `import-linter` to `[dependency-groups.dev]` in `pyproject.toml`; create `.importlinter` with the minimum-viable header `[importlinter]\nroot_package = slopmortem` (no contracts yet — `lint-imports` errors out on a file lacking `root_package`) | `pyproject.toml`, `.importlinter` |
| T0.2 | Wire `lint-imports` into `just lint`. The CI `lint` job uses `astral-sh/ruff-action` directly with no `uv sync`, so `lint-imports` must be added to the `typecheck` or `test` job (both already run `uv sync`) — not the `lint` job | `justfile`, `.github/workflows/ci.yml` |
| T0.3 | Add `just smoke` recipe that exercises typer registration + the cassette-backed eval path (the CLI has no cassette mode of its own): `uv run slopmortem --help` and `--help` for each subcommand (`ingest`, `query`, `replay`, `embed-prefetch`) — catches T3.2 regressions — followed by `just eval` (already cassette-backed) for the LLM pipeline | `justfile` |

Checkpoint: `just test && just lint && just typecheck && just smoke` green.

### PR 1 — Approach A: façade hygiene (~3 days)

Order matters. Expand façades first (additive, can't break callers), migrate
callers second, lock the door third, rename last.

| ID | Task | Files | Notes |
|---|---|---|---|
| T1.0 | **Verify the prod←evals leak is closed.** Already fixed by commit `f6557ac` (2026-05-02), which moved `NoCannedEmbeddingError` and the surrounding cassettes module to `slopmortem/llm/cassettes.py`. Re-run `grep -rnE "from slopmortem\.evals" slopmortem/` and assert no production module imports from `evals/`. If clean, no code change; T1.6's contract will pass on first run | `slopmortem/llm/fake_embeddings.py` (verify only), `slopmortem/evals/cassettes.py` (verify only) | Verification only — relocation already shipped |
| T1.1 | Expand `corpus/__init__.py` `__all__` to include the modules currently bypassed. **Verify each first** with `grep -rnE "^from slopmortem\.corpus\." slopmortem/ tests/ \| grep -v "^slopmortem/corpus/"` and only add modules with at least one outside caller. Candidates: `entity_resolution`, `paths`, `extract`, `reclassify`, `merge_text` (re-export `Section`, `combined_hash`, `combined_text` — 2 outside callers), `reconcile` / `qdrant_store.QdrantCorpus` / `qdrant_store.ensure_collection` (already exported, verify and use as the migration target in T1.5), `store`, `summarize`, `alias_graph`. **Plus a public façade for the `tools_impl` corpus-binding indirection**: introduce `set_query_corpus(corpus)` (and re-export `TAVILY_EXTRACT_URL` if still needed by `corpus/sources/tavily.py`) so `cli.py:68`, `evals/runner.py:311`, `evals/recording_helper.py:156` can drop the `from slopmortem.corpus.tools_impl import _set_corpus` deep-and-private import. **Persist the verification result** as a comment block at the top of `corpus/__init__.py` (e.g. `# T1.1 verification 2026-05-02: store=TYPE_CHECKING-only (3 sites), summarize=external (1 test), alias_graph=internal-only`) — T1.7 reads this | `corpus/__init__.py`, `corpus/tools_impl.py` (add public `set_query_corpus` wrapper) | Additive |
| T1.2 | Expand `llm/__init__.py`. Already-exported (verify, do not duplicate): `EMBED_DIMS`, `OpenAIEmbeddingClient`, `FakeEmbeddingClient`, `FastEmbedEmbeddingClient`. **Add**: `OpenRouterClient`, `make_embedder` (from `embedding_factory`), `client.CompletionResult`, `embedding_client.EmbeddingResult`, `gather_with_limit` and `is_transient_http` (from `openrouter`, currently reached directly by tests / sibling modules), the relevant `tools` and `cassettes` symbols, and the remaining fakes (`FakeLLMClient`, `FakeResponse`) | `llm/__init__.py` | Additive |
| T1.3 | Replace empty `stages/__init__.py` with re-exports of `extract_facets`, `retrieve`, `llm_rerank`, `synthesize_all`, `consolidate_risks`. **Also re-export** `synthesize` and `synthesize_prompt_kwargs` from `stages.synthesize` — currently imported directly by `tests/stages/test_synthesize.py:13`, `tests/test_observe_redaction.py:40`, `tests/test_pipeline_e2e.py:48`; without re-export the T1.5 sweep would have to rewrite those test imports instead | `stages/__init__.py` | Additive |
| T1.4 | Expand `corpus/sources/__init__.py` to re-export the 5 adapter classes alongside `Source`, `Enricher` | `corpus/sources/__init__.py` | Additive |
| T1.5 | Sweep all in-tree imports to use the façades: `from slopmortem.corpus.paths import safe_path` → `from slopmortem.corpus import safe_path`. Generate the file list with `grep -lrnE "(^\|[[:space:]])from slopmortem\.(corpus\|llm\|stages\|corpus\.sources)\." slopmortem/ tests/` (the indented form catches `TYPE_CHECKING:` blocks). The grep output is the CREATE/MODIFY boundary — do not touch files outside it | grep-derived list (~30 files in `slopmortem/` and `tests/`); brief includes the resolved list | Mechanical |
| T1.6 | Author `.importlinter` contracts: `corpus`, `llm`, `stages`, `corpus.sources`, `tracing` are leaf packages; outside imports must hit `__init__.py` only. **Also add placeholder forbidden contracts for `ingest` and `cli`** at this stage — they cover only `from slopmortem.ingest.* import …` / `from slopmortem.cli.* import …` and are tightened in T2.7 / T3.6, but they close the enforcement gap that would otherwise persist through PR 2 and PR 3 | `.importlinter` | Enforces 1.5 |
| T1.7 | Rename true internals with `_` prefix, one commit per rename for bisectable blame. **Safe-now (no outside callers verified)**: `alias_graph`→`_alias_graph`, `schema`→`_schema`. **Conditional on T1.1 verification comment**: `store` (only `TYPE_CHECKING` callers in `pipeline.py:41`, `cli.py:84`, `stages/retrieve.py:19` — cheap to migrate via the `corpus/__init__.py` re-export), `summarize` (one test caller: `tests/corpus/test_summarize.py:6`). **Deferred / unsafe as written** (do not include in this PR): `merge_text` has 2 outside callers (`ingest.py:59`, `tests/corpus/test_merge_deterministic.py:7`) — T1.1 expands the façade with `Section`/`combined_hash`/`combined_text`, so once T1.5 migrates the 2 callers a follow-up PR can underscore the module; `embed_sparse` has 3 outside callers (`ingest.py:975`, `evals/recording_helper.py:206`, `stages/retrieve.py:72`, all lazy `noqa: PLC0415` imports) — either expose `encode` via `corpus/__init__.py` and migrate the 3 callers in T1.5 first, or split out a public `corpus/sparse.py` shim before underscoring; `tools_impl` has 6+ outside callers across cli, evals, llm, sources, and tests, mixing public-looking names (`get_post_mortem`, `search_corpus`, `TAVILY_EXTRACT_URL`) with private (`_set_corpus`, `_tavily_extract`) — T1.1 introduces the `set_query_corpus` façade, but a full underscore rename requires either splitting the file or wrapping every public name. **Read T1.1's verification comment block at the top of `corpus/__init__.py` first** | `corpus/*.py` + import call sites | One file at a time |

Checkpoint after each task: `just smoke && just lint`.

End state: façades are real, deep imports fail CI, big files still big.

### PR 2 — Approach B for `ingest.py` (~3 days)

Highest-risk PR. Touches the warm-cache and journal-ordering invariants
documented in `CLAUDE.md`. Cassette suite is the safety net.

**Destination map for non-extracted top-level names** (not all of
`ingest.py`'s public surface fits cleanly into the four feature files
T2.2–T2.5; pin destinations explicitly to avoid every leftover landing in
`_orchestrator.py` by default):

- Protocols (`Corpus` `ingest.py:114`, `IngestProgress` `158`,
  `SlopClassifier` `210`) → `ingest/_protocols.py`. Keeps `_orchestrator.py`
  lean and gives the public types a stable home.
- `IngestPhase` / `INGEST_PHASE_LABELS` / `IngestResult` / `NullProgress` →
  `ingest/_orchestrator.py` (they describe the orchestrator's contract with
  callers and are imported alongside `ingest()`).
- `ProcessOutcome` (`ingest.py:675`) → `ingest/_journal_writes.py`
  (it is the return type of the per-entry write sequence).
- `_gather_entries` and `_enrich_pipeline` → `ingest/_gather.py` (a distinct
  GATHER/CLASSIFY phase that runs upstream of fan-out and slop-gating; not
  covered by T2.2–T2.5).
- `_quarantine` (`ingest.py:405`) → `ingest/_slop_gate.py` (T2.5 already
  owns the slop-gate routing; collocate the helper).
- Test/dev fakes (`InMemoryCorpus` `228`, `FakeSlopClassifier` `250`,
  `HaikuSlopClassifier` `265`) → stay in `_orchestrator.py` for now (they
  are public surface that tests depend on, ~90 LOC, splitting them out is
  optional polish for a later PR).

| ID | Task | Files | Notes |
|---|---|---|---|
| T2.1 | `git mv slopmortem/ingest.py slopmortem/ingest/_orchestrator.py`. **Add an explicit `__all__` to `_orchestrator.py`** listing only names that current external callers import (derive via `grep -rnE "from slopmortem\.ingest" slopmortem/ tests/ \| grep -v "^slopmortem/ingest/"`). Then create `slopmortem/ingest/__init__.py` with `from slopmortem.ingest._orchestrator import *`. Without `__all__` the star-import would publicly expose `InMemoryCorpus`, `FakeSlopClassifier`, `HaikuSlopClassifier`, and the `Corpus`/`SlopClassifier` Protocols — they are non-underscore today only because nothing prevented it | `slopmortem/ingest/_orchestrator.py`, `slopmortem/ingest/__init__.py` | Pure move. **Note:** `logging.getLogger(__name__)` inside `_orchestrator.py` will now resolve to `slopmortem.ingest._orchestrator` instead of `slopmortem.ingest`. Grep for log filters / span attribute keys that match the old literal name and update them, or assert in T2.1 that none exist |
| T2.2 | Extract the warm-cache block (first entry runs alone, then fan out) into `ingest/_warm_cache.py`. Header comment: "Preserves CACHE_READ_RATIO_LOW invariant — see CLAUDE.md and `slopmortem/ingest/__init__.py` callers" | `ingest/_warm_cache.py`, `ingest/_orchestrator.py` | Verify `cache_read_ratio` span event still fires |
| T2.3 | Extract the per-entry pipeline into `ingest/_fan_out.py`. Actual order is **facet → summarize → embed → upsert** — slop classification gates entries upstream of fan-out (it lives in the classify phase before any per-entry work) and belongs in `_slop_gate.py` (T2.5), not here. Today's seam: `_facet_summarize_fanout` (`ingest.py:637`) plus the `_embed_and_upsert` call inside `_process_entry` (`ingest.py:688`) | `ingest/_fan_out.py`, `ingest/_orchestrator.py` | |
| T2.4 | Extract journal write ordering into `ingest/_journal_writes.py`. **Actual order**: `journal.upsert_pending` (`ingest.py:758`) → `write_raw_atomic` (763) → `write_canonical_atomic` (780) → `corpus.delete_chunks_for_canonical` (797) → `_embed_and_upsert` Qdrant upsert (827) → `journal.mark_complete` (852). Header comment documents the invariant. Add unit tests simulating crashes at **each** write boundary: (a) crash between `upsert_pending` and `write_raw_atomic`, (b) crash between `write_raw_atomic` and `write_canonical_atomic`, (c) crash between `write_canonical_atomic` and the Qdrant upsert, (d) crash between Qdrant upsert and `mark_complete`. Assert no orphan `mark_complete`, and that any journal row stuck past `pending` has recoverable disk state matching the canonical hash | `ingest/_journal_writes.py`, `tests/ingest/test_journal_writes.py` | |
| T2.5 | Extract slop-gate quarantine routing into `ingest/_slop_gate.py`. **Add a unit test** asserting (a) entries with `slop_score > config.slop_threshold` route to `_quarantine` and produce no Qdrant point and no journal row, (b) `_PRE_VETTED_SOURCES` bypass still works. Cassettes do not cover this path because no LLM call is made on quarantined entries | `ingest/_slop_gate.py`, `ingest/_orchestrator.py`, `tests/ingest/test_slop_gate.py` | |
| T2.6 | Trim `ingest/__init__.py` to an explicit `__all__` listing only the public surface. Derive the list with `grep -rhE "from slopmortem\.ingest import [^_]" slopmortem/ tests/ \| grep -v "^slopmortem/ingest/" \| sed -E 's/.*import //' \| tr ',' '\n' \| sort -u` (callers, not `_orchestrator` top-level defs). **Exception:** the `[^_]` filter silently drops `_Point` (imported by `tests/corpus/test_qdrant_store.py:11`). Either re-route that test to import `_Point` from `slopmortem.corpus.qdrant_store` directly (preferred — `_Point` is a Qdrant payload struct, not an ingest concern) or add `_Point` to `__all__` as an explicit exception | `ingest/__init__.py` | |
| T2.7 | Add `.importlinter` contract: `slopmortem.ingest._*` is private to the `ingest` package | `.importlinter` | |

Checkpoint after each task: `just test && just smoke`. Behavior change in a
pure-move step means we stop and investigate.

### PR 3 — Approach B for `cli.py` (~2 days)

Lower risk than PR 2, but typer command registration is import-time, so
sub-task ordering matters.

`slopmortem/cli_progress.py` (extracted on the cassettes branch) **stays at
the top level** — the eval recorders (`evals/corpus_recorder.py`,
`evals/render.py`) import `RichPhaseProgress` from it, so moving it under
`cli/_progress.py` would force evals to reach into the future `cli/_internal/`
and reintroduce the leak we are closing. Subclass placement:

- `RichIngestProgress` (`cli.py:701`) is single-consumer (only `ingest_cmd`
  uses it) → move into `_ingest_cmd.py` in T3.2.
- `RichQueryProgress` (`cli.py:739`) is **dual-consumer** — both `query_cmd`
  (`cli.py:442`) and `replay_cmd` (`cli.py:800`) instantiate it, and both
  share `_QUERY_PHASE_LABELS` and `_render_query_footer`. These three names
  live in `cli/_common.py`, not in `_query_cmd.py`.

Late/lazy imports inside `cli.py` helpers (`qdrant_client`, `MergeJournal`,
`FakeSlopClassifier`, `HaikuSlopClassifier`, `ensure_collection`,
`EMBED_DIMS` — `cli.py:571, 606, 626, 629, 642, 644, 645`) exist to keep
`slopmortem --help` fast (avoid loading qdrant/onnx at import time). T3.2
and T3.3 must preserve the lazy `noqa: PLC0415` pattern verbatim — do not
hoist these to module-top imports in the extracted files.

| ID | Task | Files | Notes |
|---|---|---|---|
| T3.1 | Create `cli/__init__.py` containing the top-level typer `app` object; `git mv slopmortem/cli.py slopmortem/cli/_app.py` initially to keep the move atomic | `slopmortem/cli/` | |
| T3.2 | Extract one subcommand at a time into its own file: `_ingest_cmd.py`, `_query_cmd.py`, `_replay_cmd.py`, `_embed_prefetch_cmd.py`. These are the four `@app.command(...)` registrations in `cli.py` (`ingest`, `query`, `replay`, `embed-prefetch`). The `--reclassify` and `--reconcile` flags stay on `_ingest_cmd.py` (they are options, not subcommands); `nuke` stays in `justfile` (it is a shell recipe, not a typer command). Each file registers on the shared `app` via `app.command()`. **Import order in `cli/__init__.py` determines `--help` listing order** — preserve the current ordering: `_ingest_cmd`, `_query_cmd`, `_replay_cmd`, `_embed_prefetch_cmd` | `cli/_ingest_cmd.py`, `cli/_query_cmd.py`, `cli/_replay_cmd.py`, `cli/_embed_prefetch_cmd.py`, `cli/__init__.py`, `cli/_app.py` (shrinks then deletes) | One commit per subcommand |
| T3.3 | Move shared helpers (argument types, Rich output formatting) into `cli/_common.py` | `cli/_common.py` | |
| T3.4 | Verify `pyproject.toml` `[project.scripts]` still resolves to the new entrypoint; smoke-test every typer subcommand with `--help`. Capture `uv run slopmortem --help` output to a tempfile **before** T3.1 begins and diff against the same command after T3.5; the byte diff must be empty (this catches both missing registrations and reordered listings). Subcommand list is fixed by T3.2 — `ingest`, `query`, `replay`, `embed-prefetch` | `pyproject.toml` (verify only) | |
| T3.5 | Set `cli/__init__.py` `__all__` to `["app"]` only; delete `cli/_app.py` if empty after extractions | `cli/__init__.py` | |
| T3.6 | Add `.importlinter` contract: `slopmortem.cli._*` is private | `.importlinter` | |

Checkpoint: smoke matrix — every subcommand from `just --list` runs `--help`
cleanly; output matches before/after.

### PR 4 — Approach C: visual `_internal/` convention (~2 days, optional)

Mechanical but the largest git-history shuffle. Single PR so reviewers can
diff with `-M50%` once. Re-evaluate before starting whether the underscore
convention from PR 1 has proven sufficient.

| ID | Task | Files | Notes |
|---|---|---|---|
| T4.1 | For each of `corpus`, `llm`, `ingest`, `cli`: create `_internal/` and `git mv` every `_*.py` into it. Drop the leading underscore from the filename inside `_internal/` since the directory name carries the signal | All affected packages | Pure file moves |
| T4.2 | Update intra-package imports to reference `._internal.<module>` | Inside the four packages | |
| T4.3 | Tighten `.importlinter` contracts: outside packages may not import from `*._internal.*` (belt-and-braces with the existing `__init__.py`-only rule) | `.importlinter` | |
| T4.4 | Update `CLAUDE.md` "Layout" section to reflect the new tree | `CLAUDE.md` | |

Checkpoint: full `just test && just lint && just typecheck && just smoke && just eval`.

## Risks

- **PR 2 behavior drift.** The warm-cache and journal-ordering invariants are
  load-bearing for ingest correctness. Mitigation: cassette suite at every
  step + new unit tests in T2.4 (crash at each write boundary, not just
  `mark_complete`) and T2.5 (slop-gate quarantine — no cassette coverage
  because the path makes no LLM call). If T2.1 (pure move) shows any cassette
  diff, stop — that's a real bug.
- **Logger / span attribute drift in T2.1.** Moving `ingest.py` →
  `_orchestrator.py` shifts `__name__` for any `logging.getLogger(__name__)`
  call inside the moved file. Mitigation: T2.1 grep for log filters and
  Laminar span attribute keys that pin the literal `slopmortem.ingest` name
  and update them, or assert none exist before merging.
- **Git blame churn.** PR 1's renames and PR 4's directory moves both rewrite
  blame for the affected files. Mitigation: one commit per rename/move,
  preserved with `git mv`, so `git log --follow` and `git blame -C -C` still
  reach the original history. Reviewers diff with `-M`.
- **Out-of-tree consumers.** If notebooks, scratch scripts, or unmerged
  branches import the soon-to-be-private modules, PR 1.6 breaks them.
  Mitigation: announce in the team channel before PR 1 lands; un-rename is
  one commit.
- **PR 3 entrypoint regression.** Typer command registration depends on
  import order. Mitigation: T3.4 verifies the full subcommand surface with
  `--help` before marking PR 3 done.
- **Cassette drift entering PR 2.** If cassettes are stale at the start of
  PR 2, drift inside PR 2 is ambiguous. Mitigation: regenerate cassettes once
  on `main` before PR 2 branches off (operator-triggered, costs ~$2 per
  `just eval-record`).

## Pros / Cons of the staged approach

**Pros**

- Each PR is independently shippable; we can pause after any of them and the
  tree is in a better state than today.
- A is cheap and stops regressions while B and C are in flight.
- B isolates the highest-risk work (`ingest.py`) into its own PR with
  cassettes as the contract.
- C is opt-in — easy to defer or skip entirely.

**Cons**

- Two passes of git-history disruption (PR 1's renames, PR 4's `_internal/`
  moves) instead of one. Acceptable because PRs are weeks apart and `git mv`
  preserves follow-history.
- Five PRs is more review overhead than a single mega-refactor. Acceptable
  because each PR is small enough to review thoroughly, and a mega-refactor
  would be impossible to review at all.

## Out of scope / future work

- Splitting `pipeline.py` (405 LOC, 5 public / 7 private) — small enough to
  leave alone for now.
- Splitting `evals/runner.py` (607 LOC, 1 public / 15 private) — already well
  encapsulated at the file level (one public function), and `evals/` is
  test-infra, not prod. Revisit if a future eval feature lands here.
- Introducing a `slopmortem.api` namespace or formal SDK surface. The
  `__init__.py` façades are the API for now.
- Migrating to a different lint tool (`tach` is a similar option). `import-linter`
  picked because it's mature and the contract syntax is readable.

## Open questions

None at design time — operator already chose the full A → C path during
brainstorming.

## Execution Strategy

**Subagents.** Tasks dispatched to fresh `python-development:python-pro`
agents one at a time. The user's standing preference is sequential execution
(no parallel batching), so the dependency graph below chains all tasks
linearly within each PR even where independence would technically allow
parallelism (e.g., T1.1–T1.4 expand four different `__init__.py` files and
could run in parallel, but we run them sequentially per user preference).

Reason: the work is mechanical and low-coordination, but each task's
correctness depends on a clean checkpoint before the next starts. Sequential
dispatch keeps `main` continuously green and matches the user's preferred
workflow.

## Task Dependency Graph

```
PR 0 (tooling prep)
  T0.1 (add import-linter dep)         predecessors: none
  T0.2 (wire lint into CI)             predecessors: T0.1
  T0.3 (just smoke recipe)             predecessors: none

PR 1 (façade hygiene) — gate: PR 0 merged
  T1.0 (relocate NoCannedEmbeddingError) predecessors: T0.2
  T1.1 (expand corpus/__init__)        predecessors: T1.0
  T1.2 (expand llm/__init__)           predecessors: T1.1
  T1.3 (stages/__init__ exports)       predecessors: T1.2
  T1.4 (corpus/sources/__init__)       predecessors: T1.3
  T1.5 (sweep imports to façades)      predecessors: T1.4
  T1.6 (.importlinter contracts)       predecessors: T1.5
  T1.7 (rename internals with _)       predecessors: T1.6

PR 2 (ingest split) — gate: PR 1 merged + cassettes regenerated on main
  T2.1 (git mv ingest.py → package)    predecessors: T1.7
  T2.2 (extract _warm_cache)           predecessors: T2.1
  T2.3 (extract _fan_out)              predecessors: T2.2
  T2.4 (extract _journal_writes)       predecessors: T2.3
  T2.5 (extract _slop_gate)            predecessors: T2.4
  T2.6 (trim ingest/__init__ __all__)  predecessors: T2.5
  T2.7 (importlinter for ingest._*)    predecessors: T2.6

PR 3 (cli split) — gate: PR 2 merged
  T3.1 (cli.py → cli/ package)         predecessors: T2.7
  T3.2 (extract subcommand files)      predecessors: T3.1
  T3.3 (cli/_common.py)                predecessors: T3.2
  T3.4 (verify entrypoint + --help)    predecessors: T3.3
  T3.5 (cli/__init__ __all__)          predecessors: T3.4
  T3.6 (importlinter for cli._*)       predecessors: T3.5

PR 4 (visual _internal/) — gate: PR 3 merged + reassess if still wanted
  T4.1 (move _*.py into _internal/)    predecessors: T3.6
  T4.2 (update intra-package imports)  predecessors: T4.1
  T4.3 (tighten importlinter)          predecessors: T4.2
  T4.4 (update CLAUDE.md layout)       predecessors: T4.3
```

Total: 28 tasks (PR 0: 3, PR 1: 8, PR 2: 7, PR 3: 6, PR 4: 4), all sequential per user preference.

## Agent Assignments

```
Agent assignments (auto-selected — Python-only diff throughout):
  T0.1 — Add import-linter dep             → python-development:python-pro
  T0.2 — Wire lint into just + CI          → python-development:python-pro
  T0.3 — just smoke recipe                 → python-development:python-pro
  T1.0 — Relocate NoCannedEmbeddingError   → python-development:python-pro
  T1.1 — Expand corpus/__init__            → python-development:python-pro
  T1.2 — Expand llm/__init__               → python-development:python-pro
  T1.3 — stages/__init__ exports           → python-development:python-pro
  T1.4 — corpus/sources/__init__ exports   → python-development:python-pro
  T1.5 — Sweep imports to façades          → python-development:python-pro
  T1.6 — .importlinter contracts (PR 1)    → python-development:python-pro
  T1.7 — Rename internals with _           → python-development:python-pro
  T2.1 — git mv ingest.py to package       → python-development:python-pro
  T2.2 — Extract _warm_cache               → python-development:python-pro
  T2.3 — Extract _fan_out                  → python-development:python-pro
  T2.4 — Extract _journal_writes + test    → python-development:python-pro
  T2.5 — Extract _slop_gate                → python-development:python-pro
  T2.6 — Trim ingest/__init__ __all__      → python-development:python-pro
  T2.7 — .importlinter for ingest          → python-development:python-pro
  T3.1 — cli.py to cli/ package            → python-development:python-pro
  T3.2 — Extract subcommand files          → python-development:python-pro
  T3.3 — cli/_common.py                    → python-development:python-pro
  T3.4 — Verify entrypoint + --help        → python-development:python-pro
  T3.5 — cli/__init__ __all__              → python-development:python-pro
  T3.6 — .importlinter for cli             → python-development:python-pro
  T4.1 — Move to _internal/                → python-development:python-pro
  T4.2 — Update intra-package imports      → python-development:python-pro
  T4.3 — Tighten importlinter              → python-development:python-pro
  T4.4 — Update CLAUDE.md layout           → python-development:python-pro
```

After PR 4 lands, run the `superpowers:post-implementation-polish` skill (or its successor) over the affected packages — `corpus/`, `llm/`, `stages/`, `ingest/`, `cli/`. This is parent-session work, not a dispatched task.

Each agent brief must explicitly state: do not stage, do not commit, stay
strictly within the CREATE/MODIFY file list for the assigned task. Parent
session owns commit authorship and may bundle multiple completed tasks into
one commit at PR boundaries.
