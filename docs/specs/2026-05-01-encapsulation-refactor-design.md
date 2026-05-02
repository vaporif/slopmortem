# Encapsulation Refactor — Design

Status: design, awaiting operator approval.

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
- `from slopmortem.corpus._db import connect` — outside callers reaching into
  an `_`-prefixed module.
- `from slopmortem.corpus.sources._throttle import …` — outside callers reaching
  into an `_`-prefixed module inside an `_`-prefixed file convention.
- `from slopmortem.llm.openrouter import OpenRouterClient` — `llm/__init__.py`
  only re-exports embedding clients, so the most-used class in the package
  bypasses the façade entirely.

Two top-level files have grown past the size where one-job-per-file holds:

- `slopmortem/cli.py` — 1109 LOC, all typer subcommands in one file.
- `slopmortem/ingest.py` — 1148 LOC, 11 public top-level defs, holds the
  warm-cache pattern, fan-out, journal write ordering, and slop-gate routing
  in a single module. The load-bearing invariants documented in `CLAUDE.md`
  ("mark_complete only after both writes succeed", "first entry alone, then
  fan out") have no dedicated home.

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
| T1.1 | Expand `corpus/__init__.py` `__all__` to include the modules currently bypassed. **Verify each first** with `grep -rnE "^from slopmortem\.corpus\." slopmortem/ tests/ \| grep -v "^slopmortem/corpus/"` and only add modules with at least one outside caller. Candidates: `entity_resolution`, `paths`, `extract`, `reclassify`, `store`, `summarize`, `alias_graph`. **Persist the verification result** as a comment block at the top of `corpus/__init__.py` (e.g. `# T1.1 verification 2026-05-01: store=internal-only, summarize=external (5 sites)`) — T1.7 reads this | `corpus/__init__.py` | Additive |
| T1.2 | Expand `llm/__init__.py` to export `OpenRouterClient`, `client.CompletionResult`, `embedding_client.EmbeddingResult`, the relevant `tools` and `cassettes` symbols, and the fakes | `llm/__init__.py` | Additive |
| T1.3 | Replace empty `stages/__init__.py` with re-exports of `extract_facets`, `retrieve`, `llm_rerank`, `synthesize_all`, `consolidate_risks` | `stages/__init__.py` | Additive |
| T1.4 | Expand `corpus/sources/__init__.py` to re-export the 5 adapter classes alongside `Source`, `Enricher` | `corpus/sources/__init__.py` | Additive |
| T1.5 | Sweep all in-tree imports to use the façades: `from slopmortem.corpus.paths import safe_path` → `from slopmortem.corpus import safe_path`. Generate the file list with `grep -lrnE "(^\|[[:space:]])from slopmortem\.(corpus\|llm\|stages\|corpus\.sources)\." slopmortem/ tests/` (the indented form catches `TYPE_CHECKING:` blocks). The grep output is the CREATE/MODIFY boundary — do not touch files outside it | grep-derived list (~30 files in `slopmortem/` and `tests/`); brief includes the resolved list | Mechanical |
| T1.6 | Author `.importlinter` contracts: `corpus`, `llm`, `stages`, `corpus.sources`, `tracing` are leaf packages; outside imports must hit `__init__.py` only. **Also add placeholder forbidden contracts for `ingest` and `cli`** at this stage — they cover only `from slopmortem.ingest.* import …` / `from slopmortem.cli.* import …` and are tightened in T2.7 / T3.6, but they close the enforcement gap that would otherwise persist through PR 2 and PR 3 | `.importlinter` | Enforces 1.5 |
| T1.7 | Rename true internals with `_` prefix, one commit per rename for bisectable blame: `alias_graph`→`_alias_graph`, `embed_sparse`→`_embed_sparse`, `merge_text`→`_merge_text`, `schema`→`_schema`, `tools_impl`→`_tools_impl`. **Read T1.1's verification comment block at the top of `corpus/__init__.py` first**; skip `store` and `summarize` renames if that record shows external use | `corpus/*.py` + import call sites | One file at a time |

Checkpoint after each task: `just smoke && just lint`.

End state: façades are real, deep imports fail CI, big files still big.

### PR 2 — Approach B for `ingest.py` (~3 days)

Highest-risk PR. Touches the warm-cache and journal-ordering invariants
documented in `CLAUDE.md`. Cassette suite is the safety net.

| ID | Task | Files | Notes |
|---|---|---|---|
| T2.1 | `git mv slopmortem/ingest.py slopmortem/ingest/_orchestrator.py`. **Add an explicit `__all__` to `_orchestrator.py`** listing only names that current external callers import (derive via `grep -rnE "from slopmortem\.ingest" slopmortem/ tests/ \| grep -v "^slopmortem/ingest/"`). Then create `slopmortem/ingest/__init__.py` with `from slopmortem.ingest._orchestrator import *`. Without `__all__` the star-import would publicly expose `InMemoryCorpus`, `FakeSlopClassifier`, `HaikuSlopClassifier`, and the `Corpus`/`SlopClassifier` Protocols — they are non-underscore today only because nothing prevented it | `slopmortem/ingest/_orchestrator.py`, `slopmortem/ingest/__init__.py` | Pure move. **Note:** `logging.getLogger(__name__)` inside `_orchestrator.py` will now resolve to `slopmortem.ingest._orchestrator` instead of `slopmortem.ingest`. Grep for log filters / span attribute keys that match the old literal name and update them, or assert in T2.1 that none exist |
| T2.2 | Extract the warm-cache block (first entry runs alone, then fan out) into `ingest/_warm_cache.py`. Header comment: "Preserves CACHE_READ_RATIO_LOW invariant — see CLAUDE.md and `slopmortem/ingest/__init__.py` callers" | `ingest/_warm_cache.py`, `ingest/_orchestrator.py` | Verify `cache_read_ratio` span event still fires |
| T2.3 | Extract per-entry pipeline (slop → facet → embed → upsert) into `ingest/_fan_out.py` | `ingest/_fan_out.py`, `ingest/_orchestrator.py` | |
| T2.4 | Extract journal write ordering (qdrant → disk → mark_complete) into `ingest/_journal_writes.py`. Header comment documents the invariant. Add unit tests simulating crashes at **each** write boundary, not just `mark_complete`: (a) crash between `write_raw_atomic` and `write_canonical_atomic`, (b) crash between `write_canonical_atomic` and Qdrant upsert, (c) crash between Qdrant upsert and `mark_complete`. Assert no orphan `mark_complete` and no journal row stuck past `pending` without recoverable disk state | `ingest/_journal_writes.py`, `tests/ingest/test_journal_writes.py` | |
| T2.5 | Extract slop-gate quarantine routing into `ingest/_slop_gate.py`. **Add a unit test** asserting (a) entries with `slop_score > config.slop_threshold` route to `_quarantine` and produce no Qdrant point and no journal row, (b) `_PRE_VETTED_SOURCES` bypass still works. Cassettes do not cover this path because no LLM call is made on quarantined entries | `ingest/_slop_gate.py`, `ingest/_orchestrator.py`, `tests/ingest/test_slop_gate.py` | |
| T2.6 | Trim `ingest/__init__.py` to an explicit `__all__` listing only the public surface. Derive the list with `grep -rhE "from slopmortem\.ingest import [^_]" slopmortem/ tests/ \| grep -v "^slopmortem/ingest/" \| sed -E 's/.*import //' \| tr ',' '\n' \| sort -u` (callers, not `_orchestrator` top-level defs) | `ingest/__init__.py` | |
| T2.7 | Add `.importlinter` contract: `slopmortem.ingest._*` is private to the `ingest` package | `.importlinter` | |

Checkpoint after each task: `just test && just smoke`. Behavior change in a
pure-move step means we stop and investigate.

### PR 3 — Approach B for `cli.py` (~2 days)

Lower risk than PR 2, but typer command registration is import-time, so
sub-task ordering matters.

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
  T1.1 (expand corpus/__init__)        predecessors: T0.2
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

Total: 27 tasks (PR 0: 3, PR 1: 7, PR 2: 7, PR 3: 6, PR 4: 4), all sequential per user preference.

## Agent Assignments

```
Agent assignments (auto-selected — Python-only diff throughout):
  T0.1 — Add import-linter dep             → python-development:python-pro
  T0.2 — Wire lint into just + CI          → python-development:python-pro
  T0.3 — just smoke recipe                 → python-development:python-pro
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
