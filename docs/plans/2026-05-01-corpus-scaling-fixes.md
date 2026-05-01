# Corpus scaling fixes

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax — these are **persistent durable state**, not visual decoration. The executor edits the plan file in place: `- [ ]` → `- [x]` the instant a step verifies, before moving on. On resume, the executor scans existing `- [x]` marks and skips them — these steps are NOT redone.

**Goal:** Remove the per-ingest journal full-scans and the per-chunk Qdrant/sparse-encoder serial loops that make ingest strictly quadratic in corpus size and freeze the event loop on every chunk. Add the missing `aliases.canonical_id` index, move the per-call DDL to init-time, and add a defensive `CapacityLimiter` around the synth fan-out.

**Why:** Validated audit (`docs/plans/...` not yet written; see conversation that produced this plan) confirmed:

- Each ingest of one entry runs **2× `SELECT * FROM merge_journal` with no LIMIT** in `entity_resolution.py:453`/`:604`/`:634` — O(M·N) ingest. At N=100K canonicals × M=1K backfill that's ~2×10⁸ Python row materializations and the dominant cost ceiling for ingest scale-up.
- The tier-3 sibling lookup encodes sector as a `::sector` suffix on `canonical_id` and Python-`endswith`-scans every row — sqlite cannot help (`entity_resolution.py:634-642`).
- `aliases` table has no index on `canonical_id` (`merge.py:67-75`); each alias-fetch on the query path runs a full table scan **plus three PRAGMAs** because every call opens a fresh sqlite connection (`_db.py:12-19`). At K=30 candidates × 1M alias rows, this is hundreds of ms per query.
- Per-chunk `await corpus.upsert_chunk(point)` (`ingest.py:603-615`) is one HTTP RTT per chunk; ~10 chunks/doc × 10K docs = 100K serial Qdrant round-trips.
- Sparse BM25 encoder is sync on the event loop and runs per-text (`embed_sparse.py:30-34`); the `ingest_concurrency=20` limiter is **defeated** because all 20 workers serialize on the calling task's event loop when sparse-encoding.
- `_ensure_tier3_table_sync` (`entity_resolution.py:516-517`) opens a fresh sqlite connection and runs `CREATE TABLE IF NOT EXISTS` on **every** `resolve_entity` call.
- Synth fan-out has no `CapacityLimiter` (`synthesize.py:211-213`, `concurrency.py:20-22` — `gather_resilient` is plain `asyncio.gather(*aws, return_exceptions=True)`). Latent today (default `N_synthesize=5`); a config bump hits Sonnet's per-org RPM with no smoothing.
- `reclassify.py:75` reads quarantined bodies with sync `read_text()` on the event loop, blocking pre-fan-out at 10K+ files.

**Tech Stack:** Python 3.13, anyio, sqlite3 (WAL), pytest, pytest-asyncio, qdrant-client, fastembed, pydantic-settings.

## Priority — what's actually load-bearing

Not all tasks below are equal. If scope pressure hits, ship in this order:

| Task | Type | Impact at N=100K corpus | Skip if scope shrinks? |
|---|---|---|---|
| **Task 2** (indexed entity-resolution queries) | Asymptotic | Only fix that prevents O(M·N) ingest blowup. Without it, nothing else matters — ingest stops finishing | **No. Non-negotiable.** |
| **Task 3** (batched Qdrant upsert) | Constant factor (~30×) | ~16 min of network sit-time per 10K-doc backfill → ~30 s | **No.** Compounds with Task 4. |
| **Task 4** (sparse encoder batch + thread) | Constant factor + parallelism | Restores `ingest_concurrency=20` (currently defeated by sync sparse on the event loop) | **No.** |
| **Task 1** (schema indexes) | Foundation | Aliases index = ~500 ms off every query at 1M alias rows. Tasks 2-4 depend on the schema work | **No** (foundation). |
| **Task 5** (synth `CapacityLimiter`) | Defensive | Zero unless `N_synthesize` is bumped | Yes, defer if needed. |
| **Task 6** (reclassify async fixes) | Operational | Manual cadence only; not on hot path | Yes, defer if needed. |

**The one task you'd ship if you could ship only one: Task 2.** Tasks 3, 4, and Task 1's aliases index are the next tier — they make a workable system fast.

## Execution Strategy

**Sequential, single-session.** Per project preference: do not parallelize tasks, do not dispatch parallel agents. The schema/index work in Task 1 lands first because Tasks 2/3 depend on it. After each task, run targeted tests and confirm they pass before moving to the next.

Each task lists explicit **CREATE / MODIFY / DELETE** files. Stay within that list — no tangential dep bumps, refactors, or "small wins" outside the listed scope.

---

## Task 1: Schema — add `aliases` index, sector expression index, and move `tier3_decisions` DDL into init

**Files:**
- Modify: `slopmortem/corpus/merge.py`
- Modify: `slopmortem/corpus/entity_resolution.py`
- Modify: `tests/corpus/test_merge_journal.py` (or wherever the journal schema is unit-tested — verify with `rg "MergeJournal" tests/`)

- [ ] **Step 1: Add `aliases_canonical_idx` to `_SCHEMA`**

In `slopmortem/corpus/merge.py`, append a new entry to the `_SCHEMA` tuple (after the `aliases` `CREATE TABLE` block, lines 67-75):

```python
"""
CREATE INDEX IF NOT EXISTS aliases_canonical_idx
  ON aliases(canonical_id)
""",
```

`IF NOT EXISTS` makes this idempotent — existing journals pick up the index on the next `MergeJournal.init()` call, no migration script needed.

- [ ] **Step 2: Add a partial expression index on the sector suffix of `merge_journal.canonical_id`**

Append to `_SCHEMA`:

```python
"""
CREATE INDEX IF NOT EXISTS merge_sector_idx
  ON merge_journal(
    substr(canonical_id, instr(canonical_id, '::') + 2),
    merge_state
  )
  WHERE instr(canonical_id, '::') > 0
""",
```

This is a SQLite **partial expression index** (expression indexes since 3.9.0; `WHERE` clause since 3.8.0). It indexes the `::sector` suffix without requiring a denormalized column or backfill.

**Why the partial-index `WHERE` clause is load-bearing**: tier-1 canonical_ids use the registrable domain (no `::` separator, e.g. `acme.com`); tier-2 ids use `name::sector`. For a tier-1 id `"abc"`, `instr` returns 0 and `substr(canonical_id, 2)` returns `"bc"` — which would falsely match a sector named `"bc"`. The `WHERE instr(canonical_id, '::') > 0` predicate excludes tier-1 ids from the index entirely. Task 2 Step 4 mirrors the same guard in the query so the planner picks this index.

- [ ] **Step 3: Move `tier3_decisions` table DDL into `_SCHEMA`**

In `slopmortem/corpus/entity_resolution.py`, delete `_TIER3_DECISIONS_SCHEMA` (lines 204-213) and `_ensure_tier3_table_sync` (lines 216-218).

In `slopmortem/corpus/merge.py`, append the DDL to `_SCHEMA`:

```python
"""
CREATE TABLE IF NOT EXISTS tier3_decisions (
    pair_key                TEXT PRIMARY KEY,
    decision                TEXT NOT NULL,
    rationale               TEXT,
    haiku_model_id          TEXT NOT NULL,
    tiebreaker_prompt_hash  TEXT NOT NULL,
    decided_at              TEXT NOT NULL
)
""",
```

In `slopmortem/corpus/entity_resolution.py`, delete the `await to_thread.run_sync(_ensure_tier3_table_sync, db_path)` call at line 517.

- [ ] **Step 4: Verify schema test still passes; add an index-presence assertion**

Locate the existing journal-schema test (likely `tests/corpus/test_merge_journal.py`). Add:

```python
def test_init_creates_aliases_canonical_index(tmp_path):
    journal = MergeJournal(tmp_path / "journal.sqlite")
    anyio.run(journal.init)
    with sqlite3.connect(tmp_path / "journal.sqlite") as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )}
    assert "aliases_canonical_idx" in names
    assert "merge_sector_idx" in names
```

Add an analogous `tier3_decisions` table-presence check.

- [ ] **Step 5: Run targeted tests**

```
just test tests/corpus/test_merge_journal.py
just test tests/corpus/test_entity_resolution.py
```

Both must pass. Then `just lint` and `just typecheck`.

---

## Task 2: Replace `journal.fetch_all()` callsites with indexed point queries

**Files:**
- Modify: `slopmortem/corpus/merge.py`
- Modify: `slopmortem/corpus/entity_resolution.py`
- Modify: `tests/corpus/test_merge_journal.py`
- Modify: `tests/corpus/test_entity_resolution.py`

- [ ] **Step 1: Add three indexed query methods to `MergeJournal`**

In `slopmortem/corpus/merge.py`, add (next to the existing `fetch_aliases` / `fetch_all` methods):

```python
async def canonical_exists_in_states(
    self, canonical_id: str, states: tuple[str, ...]
) -> bool:
    """Indexed existence check. Uses the canonical_id PK prefix."""
    return await to_thread.run_sync(
        self._canonical_exists_in_states_sync, canonical_id, states
    )

def _canonical_exists_in_states_sync(
    self, canonical_id: str, states: tuple[str, ...]
) -> bool:
    placeholders = ",".join("?" * len(states))
    with connect(self._db) as conn:
        cur = conn.execute(
            f"""
            SELECT 1 FROM merge_journal
             WHERE canonical_id = ?
               AND merge_state IN ({placeholders})
             LIMIT 1
            """,
            (canonical_id, *states),
        )
        return cur.fetchone() is not None

async def canonical_exists(self, canonical_id: str) -> bool:
    """State-agnostic existence check. PK prefix scan."""
    return await to_thread.run_sync(self._canonical_exists_sync, canonical_id)

def _canonical_exists_sync(self, canonical_id: str) -> bool:
    with connect(self._db) as conn:
        cur = conn.execute(
            "SELECT 1 FROM merge_journal WHERE canonical_id = ? LIMIT 1",
            (canonical_id,),
        )
        return cur.fetchone() is not None

async def fetch_sector_siblings(
    self,
    sector: str,
    states: tuple[str, ...],
    *,
    exclude_canonical_id: str | None = None,
) -> list[str]:
    """List canonical_ids whose ``::sector`` suffix matches *sector* in given states.

    Uses the ``merge_sector_idx`` expression index on
    ``substr(canonical_id, instr(canonical_id, '::') + 2)``.
    """
    return await to_thread.run_sync(
        self._fetch_sector_siblings_sync, sector, states, exclude_canonical_id
    )

def _fetch_sector_siblings_sync(
    self,
    sector: str,
    states: tuple[str, ...],
    exclude_canonical_id: str | None,
) -> list[str]:
    placeholders = ",".join("?" * len(states))
    # The instr() > 0 guard mirrors the partial-index WHERE clause
    # on merge_sector_idx — both must match for the planner to pick
    # the index, and it's also a correctness guard against tier-1
    # ids (which have no '::' separator) being falsely matched.
    sql = f"""
        SELECT DISTINCT canonical_id FROM merge_journal
         WHERE instr(canonical_id, '::') > 0
           AND substr(canonical_id, instr(canonical_id, '::') + 2) = ?
           AND merge_state IN ({placeholders})
    """
    params: list[Any] = [sector.lower(), *states]
    if exclude_canonical_id is not None:
        sql += " AND canonical_id != ?"
        params.append(exclude_canonical_id)
    with connect(self._db) as conn:
        return [r[0] for r in conn.execute(sql, params)]
```

Notes:
- All three methods open a fresh connection via `connect()` — same pattern as `_fetch_aliases_sync`. The PRAGMA cost is acknowledged (see "Out of scope" — connection reuse is a separate change).
- `fetch_sector_siblings` returns `DISTINCT canonical_id` because a single canonical can have multiple `(source, source_id)` rows; the legacy code de-dupes implicitly via list comprehension over a fetched-once-per-call set, but the expression-index query can return duplicates without `DISTINCT`.

- [ ] **Step 2: Replace the `_is_parent_subsidiary_suspect` full scan**

In `slopmortem/corpus/entity_resolution.py:453-460`, replace:

```python
rows = await journal.fetch_all()
domain_present = any(
    row["canonical_id"] == domain
    and row["merge_state"] in (MergeState.COMPLETE.value, MergeState.PENDING.value)
    for row in rows
)
if not domain_present:
    return False
```

with:

```python
domain_present = await journal.canonical_exists_in_states(
    domain,
    (MergeState.COMPLETE.value, MergeState.PENDING.value),
)
if not domain_present:
    return False
```

- [ ] **Step 3: Replace the post-tier-3 `is_existing` full scan**

In `slopmortem/corpus/entity_resolution.py:604-606`, replace:

```python
existing = await journal.fetch_all()
is_existing = any(row["canonical_id"] == candidate_id for row in existing)
action: Literal["create", "merge"] = "merge" if is_existing else "create"
```

with:

```python
is_existing = await journal.canonical_exists(candidate_id)
action: Literal["create", "merge"] = "merge" if is_existing else "create"
```

- [ ] **Step 4: Replace the tier-3 sibling scan**

In `slopmortem/corpus/entity_resolution.py:634-642`, replace:

```python
rows = await journal.fetch_all()
same_sector_suffix = f"::{sector.lower()}"
siblings = [
    row["canonical_id"]
    for row in rows
    if str(row["canonical_id"]).endswith(same_sector_suffix)
    and row["canonical_id"] != candidate_id
    and row["merge_state"] in (MergeState.COMPLETE.value, MergeState.PENDING.value)
]
```

with:

```python
siblings = await journal.fetch_sector_siblings(
    sector,
    (MergeState.COMPLETE.value, MergeState.PENDING.value),
    exclude_canonical_id=candidate_id,
)
```

- [ ] **Step 5: Add unit tests for the three new methods**

In `tests/corpus/test_merge_journal.py`:

```python
async def test_canonical_exists_in_states_returns_true_when_present(...):
    # seed two rows with COMPLETE/PENDING and one with FAILED
    # assert COMPLETE→True, FAILED-only→False

async def test_canonical_exists_state_agnostic(...):
    # any state should return True

async def test_fetch_sector_siblings_uses_expression_index(...):
    # seed canonicals "alpha::fintech", "beta::fintech", "gamma::healthcare"
    # assert fetch_sector_siblings("fintech", PENDING|COMPLETE) returns
    # both fintech ids; healthcare excluded; exclude_canonical_id honored

async def test_fetch_sector_siblings_excludes_tier1_ids(...):
    # CRITICAL correctness regression test for the instr() > 0 guard.
    # Seed a tier-1 id "ab" (no '::') alongside tier-2 ids
    # "alpha::fintech" and "beta::healthcare". Calling
    # fetch_sector_siblings("b", ...) without the guard would falsely
    # return "ab" because substr("ab", 2) == "b". Assert tier-1 ids are
    # never returned regardless of sector argument.

async def test_fetch_sector_siblings_index_planner_check(tmp_path):
    # Use EXPLAIN QUERY PLAN to assert the planner picks merge_sector_idx
    # (defensive; catches accidental schema regression). The query AND
    # the index share the instr() > 0 predicate — without the predicate
    # match, the planner falls back to a full scan.
```

- [ ] **Step 6: Verify entity-resolution behavior unchanged**

`tests/corpus/test_entity_resolution.py` already covers tier-1/tier-2/tier-3 paths. Run:

```
just test tests/corpus/test_entity_resolution.py
just test tests/corpus/test_merge_journal.py
just test tests/test_ingest_idempotency.py
just test tests/test_ingest_orchestration.py
```

All must pass. Behavior is unchanged; only the SQL path differs.

- [ ] **Step 7: Run full suite**

```
just test
just lint
just typecheck
```

---

## Task 3: Batched Qdrant upsert in `_embed_and_upsert`

**Files:**
- Modify: `slopmortem/ingest.py`
- Modify: `slopmortem/corpus/qdrant_store.py`
- Modify: `tests/fakes/corpus.py` (if `2026-05-01-cleanup-findings.md` Fix 2 has shipped — otherwise modify the in-tree `InMemoryCorpus` at `slopmortem/ingest.py:220`; verify before editing)
- Modify: `tests/test_ingest_idempotency.py`
- Modify: `tests/corpus/test_qdrant_setup.py`

- [ ] **Step 1: Extend the ingest-side Corpus protocol with a batched upsert**

Locate the ingest-side `Corpus` (or `IngestCorpus`, depending on whether the cleanup-findings rename has shipped) Protocol around `slopmortem/ingest.py:115`. Add a method:

```python
async def upsert_chunks(self, points: Sequence[_Point]) -> None:
    """Upsert many chunks in one Qdrant call. Empty list is a no-op."""
    ...
```

Keep `upsert_chunk` on the protocol — `reconcile`-class-(a) repair may still want a single-point write. Mark it as the slow path in its docstring.

- [ ] **Step 2: Implement `upsert_chunks` on `QdrantCorpus`**

In `slopmortem/corpus/qdrant_store.py` (next to `upsert_chunk` around lines 318-343):

```python
async def upsert_chunks(self, points: Sequence[_Point]) -> None:
    if not points:
        return
    structs = [
        PointStruct(id=p.id, vector=p.vector, payload=p.payload)
        for p in points
    ]
    await self._client.upsert(
        collection_name=self._collection_name, points=structs
    )
```

- [ ] **Step 3: Rewrite `_embed_and_upsert` to build the points list in one pass**

In `slopmortem/ingest.py` (the function around line 595-617), replace the per-chunk loop:

```python
for c, vec in zip(chunks, embed_result.vectors, strict=True):
    sparse = sparse_encoder(c.text)
    point_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{canonical_id}:{c.chunk_idx}").hex
    payload_dict = payload.model_dump(mode="json")
    payload_dict["canonical_id"] = canonical_id
    payload_dict["chunk_idx"] = c.chunk_idx
    payload_dict["text_id"] = text_id
    point = _Point(
        id=point_id,
        vector={"dense": vec, "sparse": sparse},
        payload=payload_dict,
    )
    await corpus.upsert_chunk(point)
```

with:

```python
sparse_vectors = await sparse_encoder.encode_batch(  # populated in Task 4
    [c.text for c in chunks]
)
points: list[_Point] = []
for c, vec, sparse in zip(
    chunks, embed_result.vectors, sparse_vectors, strict=True
):
    point_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{canonical_id}:{c.chunk_idx}").hex
    payload_dict = payload.model_dump(mode="json")
    payload_dict["canonical_id"] = canonical_id
    payload_dict["chunk_idx"] = c.chunk_idx
    payload_dict["text_id"] = text_id
    points.append(
        _Point(
            id=point_id,
            vector={"dense": vec, "sparse": sparse},
            payload=payload_dict,
        )
    )
await corpus.upsert_chunks(points)
```

(The `encode_batch` call lands in Task 4. If Task 4 is not yet done at execution time, temporarily keep the per-chunk `sparse_encoder(c.text)` call inside the loop — Task 4 will swap it.)

- [ ] **Step 4: Update `InMemoryCorpus` test fake**

Add `upsert_chunks` that loops `upsert_chunk` (single-point list is fine for the fake — fidelity, not perf, is the goal here).

- [ ] **Step 5: Targeted tests**

`tests/test_ingest_idempotency.py` already verifies that re-ingest is a no-op; that test now exercises the batched path. Add one more test:

```python
async def test_embed_and_upsert_uses_batched_qdrant_call(...):
    # Use a recording fake that counts upsert_chunks vs upsert_chunk
    # invocations; assert exactly one upsert_chunks call per doc with
    # len(points) == len(chunks), zero upsert_chunk calls.
```

- [ ] **Step 6: Run suite**

```
just test
just lint
just typecheck
```

---

## Task 4: Sparse encoder — add `encode_batch` and run on a worker thread

**Files:**
- Modify: `slopmortem/corpus/embed_sparse.py`
- Modify: `slopmortem/stages/retrieve.py`
- Modify: `slopmortem/ingest.py`
- Modify: `tests/corpus/test_embed_sparse.py` (or equivalent — verify with `rg "embed_sparse" tests/`)

- [ ] **Step 1: Add an async batched encoder in `embed_sparse.py`**

Replace the body of `embed_sparse.py` (preserving the `_get_model` lazy singleton and `encode` for any remaining single-text callers — do NOT delete `encode` until call sites are confirmed migrated):

```python
import anyio


async def encode_batch(texts: Sequence[str]) -> list[dict[int, float]]:
    """Encode many texts in one ONNX batch, off the event loop."""
    if not texts:
        return []
    return await anyio.to_thread.run_sync(_encode_batch_sync, list(texts))


def _encode_batch_sync(texts: list[str]) -> list[dict[int, float]]:
    model = _get_model()
    embeddings = list(model.embed(texts))
    return [
        dict(zip(emb.indices.tolist(), emb.values.tolist(), strict=True))
        for emb in embeddings
    ]
```

Do not change the public `encode(text)` function in this step — Step 4 will decide whether to keep it.

- [ ] **Step 2: Wrap retrieve's single-query call in a thread**

In `slopmortem/stages/retrieve.py:81`:

```python
sparse = sparse_encoder(description)
```

becomes:

```python
sparse = (await encode_batch([description]))[0]
```

Update the `sparse_encoder` parameter type at `retrieve.py:45` to be the async batch function (or keep it as `SparseEncoder` and have the default callable wrap to `encode_batch`). Verify the `Protocol`/type alias for `SparseEncoder` and update its signature; tests passing a fake encoder will need updating.

If the type-surface change is too invasive, inline `from slopmortem.corpus.embed_sparse import encode_batch` at the call site and skip the parameter; document the trade-off in the test override path.

- [ ] **Step 3: Hook batched encode into `_embed_and_upsert`**

The Task 3 Step 3 rewrite already references `sparse_encoder.encode_batch(...)`. Adjust the parameter passing in `slopmortem/ingest.py` so the callable received is the `encode_batch` async function:

```python
# Where _embed_and_upsert is invoked, pass embed_sparse.encode_batch
# (not embed_sparse.encode).
```

- [ ] **Step 4: Decide on the legacy `encode(text)` API**

After Steps 2-3, `grep -rn "embed_sparse.encode\b" slopmortem/ tests/` to find any remaining callers. If none in production, mark `encode(text)` deprecated in its docstring and leave it for tests. If tests still use it, leave it untouched. Do not refactor test fakes in this task.

- [ ] **Step 5: Tests**

Add a test that asserts batch behavior:

```python
async def test_encode_batch_runs_off_event_loop(...):
    # Use anyio.to_thread.current_default_thread_limiter or a wrapped
    # sentinel to prove the call ran on a worker thread, not inline.
    # Or: assert wall-clock concurrency between two encode_batch tasks
    # (started_at vs ended_at recorded by a fake _get_model).

async def test_encode_batch_preserves_order(...):
    # Encode ["foo", "bar", "baz"]; assert returned list aligns by index.
```

- [ ] **Step 6: Run suite**

```
just test
just lint
just typecheck
```

Manual sanity:

```
just ingest                # should be visibly faster on a 50-doc batch;
                           # event loop no longer stalls on each chunk
```

---

## Task 5: Defensive `CapacityLimiter` around synthesis fan-out

**Files:**
- Modify: `slopmortem/config.py`
- Modify: `slopmortem/stages/synthesize.py`
- Modify: `tests/stages/test_synthesize.py` (or equivalent)

- [ ] **Step 1: Add `synthesize_concurrency` to config**

In `slopmortem/config.py` (next to `ingest_concurrency`):

```python
synthesize_concurrency: int = 5
"""Max concurrent synthesize tool-loops. Defensive cap; protects against
provider RPM limits when N_synthesize is bumped. Defaults to 5 to match
the common N_synthesize default."""
```

Add a validator that `synthesize_concurrency >= 1`. Mirror any existing pattern in the file.

- [ ] **Step 2: Wrap the fan-out**

In `slopmortem/stages/synthesize.py:211-213`, replace:

```python
first = await _run_one(candidates[0])
rest_results = await gather_resilient(*(_run_one(c) for c in candidates[1:]))
```

with:

```python
import anyio  # noqa: PLC0415 — local to keep top imports lean
limiter = anyio.CapacityLimiter(config.synthesize_concurrency)

async def _run_one_limited(candidate: Candidate) -> Synthesis:
    async with limiter:
        return await _run_one(candidate)

first = await _run_one_limited(candidates[0])
rest_results = await gather_resilient(
    *(_run_one_limited(c) for c in candidates[1:])
)
```

The first call still runs alone (cache-warm pattern preserved); the limiter only bounds the rest.

- [ ] **Step 3: Test**

Add a test using a fake LLM client that records concurrent in-flight call count. Set `synthesize_concurrency=2`, run with 5 candidates, assert peak in-flight ≤ 2.

- [ ] **Step 4: Run suite**

```
just test
just lint
just typecheck
```

---

## Task 6: Fix sync file reads + missing limiter in reclassify

**Files:**
- Modify: `slopmortem/corpus/reclassify.py`
- Modify: `tests/test_reclassify.py`

- [ ] **Step 1: Wrap quarantine body reads in `to_thread.run_sync`**

In `slopmortem/corpus/reclassify.py` around line 75 (verify exact line — it's inside `_row_to_pending`), replace the sync `quarantine_path.read_text(encoding="utf-8")` with `await anyio.to_thread.run_sync(quarantine_path.read_text, "utf-8")` (or equivalent — match the project's existing thread-offload pattern, e.g., the dense-embed call in `fastembed_client.py:97`).

If `_row_to_pending` is currently sync, make it async. Update its callers (`reclassify.py:138-147`).

- [ ] **Step 2: Add a `CapacityLimiter` around `_score_all`'s gather**

In `_score_all` (around lines 88-105), replace the bare `gather_resilient` with a limiter-wrapped version using `config.synthesize_concurrency` (or a new `reclassify_concurrency` knob if you prefer — be consistent with what Task 5 added). Reuse the limiter pattern from Task 5 Step 2.

- [ ] **Step 3: Tests**

`tests/test_reclassify.py` exists. Add a test that exercises a 10-row quarantine and asserts peak in-flight scorer calls ≤ limit. Confirm existing reclassify tests still pass.

- [ ] **Step 4: Run suite**

```
just test
just lint
just typecheck
```

---

## Test plan

The existing suite already covers most of the affected behavior. The tests called out as **write-before** below are the ones that don't exist today and protect the specific regression risks of this plan — write them first (TDD-style) so they fail on `main` and pass after the change.

### Existing regression catches (do not modify, just keep green)

| Task | Existing tests that fail loudly if behavior drifts |
|---|---|
| 1 (schema) | `tests/corpus/test_merge_journal.py::test_pending_then_complete`, `::test_upsert_alias_blocked_atomic` |
| 2 (indexed queries) | **All 13 tests** in `tests/corpus/test_entity_resolution.py` — tier-1, recycled-domain, parent-subsidiary, tier-3 high/band/below, decision cache, resolver flip. Every `fetch_all()` call path is exercised. |
| 3 (batched upsert) | `tests/test_ingest_idempotency.py::test_ingest_twice_no_duplicate_points` |
| 4 (sparse async) | `tests/test_pipeline_e2e.py::test_run_query_forwards_sparse_encoder` (will fail on signature change until lockstep updates ship — that's the catch) |
| 5 (synth limiter) | `tests/stages/test_synthesize.py::test_synthesize_all_warms_cache_before_gather` |
| 6 (reclassify) | All 3 tests in `tests/test_reclassify.py` |
| End-to-end | `tests/test_pipeline_e2e.py::test_full_pipeline_with_fake_clients` (cassette replay through full query path) |

### New tests to write **before** the code change (write-before)

**Before Task 1 — add to `tests/corpus/test_merge_journal.py`:**
- `test_init_creates_aliases_canonical_index` — assert `aliases_canonical_idx` is in `sqlite_master` after `init()`.
- `test_init_creates_merge_sector_partial_index` — assert the index exists AND its `sql` column contains `WHERE instr`. Locks the partial-index shape.
- `test_init_creates_tier3_decisions_table` — locks the move from per-call to init-time.

**Before Task 2 — add to `tests/corpus/test_merge_journal.py`:**
- `test_fetch_sector_siblings_excludes_tier1_ids` — **load-bearing correctness test.** Seed canonical_id `"ab"` (no `::`) alongside `"alpha::fintech"`. Call `fetch_sector_siblings("b", ...)` and assert it returns `[]`, not `["ab"]`. Catches the tier-1-id false-match bug.
- `test_fetch_sector_siblings_uses_partial_index` — run `EXPLAIN QUERY PLAN` on the live query, assert output mentions `merge_sector_idx`. Catches planner mismatch (e.g., a future edit drops `instr() > 0` from one side).
- `test_canonical_exists_in_states_uses_pk_prefix` — `EXPLAIN QUERY PLAN` shows index/PK use, not `SCAN`.

**Before Task 3 — add to `tests/test_ingest_idempotency.py` (or a new `tests/test_ingest_batched_upsert.py`):**
- `test_embed_and_upsert_calls_upsert_chunks_once_per_doc` — recording fake counts call sites; assert exactly one `upsert_chunks` per doc with `len(points) == len(chunks)`, zero `upsert_chunk` calls.
- `test_embed_and_upsert_journal_stays_pending_on_qdrant_failure` — fake `upsert_chunks` raises; assert journal stays `pending` so reconcile can recover. Locks the partial-failure contract.

**Before Task 4 — create new file `tests/corpus/test_embed_sparse.py` (does not exist today):**
- `test_encode_batch_preserves_input_order` — `["foo", "bar", "baz"]` → 3 vectors in input order.
- `test_encode_batch_runs_off_event_loop` — concurrent `encode_batch` calls overlap in wall-clock, proving thread offload (use `time.monotonic()` or a sentinel that captures call timing).
- `test_encode_batch_empty_input_returns_empty` — degenerate-case guard.

**Before Task 5 — add to `tests/stages/test_synthesize.py`:**
- `test_synthesize_all_respects_capacity_limit` — fake LLM client records peak in-flight; set `synthesize_concurrency=2` with 5 candidates, assert peak ≤ 2.

**Before Task 6 — add to `tests/test_reclassify.py`:**
- `test_reclassify_does_not_block_event_loop` — wall-clock proof that scoring runs concurrently with other coroutines (assert via overlapping timestamps from a co-running task).
- `test_reclassify_score_fanout_respects_limiter` — peak in-flight ≤ configured cap.

### Lockstep updates required by Task 4 (signature change ripple)

`embed_sparse.encode` is monkeypatched in **7 existing call sites**. They all break on the same commit until updated to use `encode_batch`:

```
tests/test_pipeline_e2e.py:364, 458, 492, 564, 704
tests/test_observe_redaction.py:299
tests/test_recording.py:136-141
```

**Pre-merge gate:** `rg -n "embed_sparse\.encode\b" tests/` must return zero results before Task 4 is considered done.

### Coverage summary

|  | Existing | Write-before | Lockstep updates |
|---|---|---|---|
| Task 1 (schema) | 1 | 3 | 0 |
| Task 2 (indexed queries) | 13 | 3 | 0 |
| Task 3 (batched upsert) | 1 | 2 | 0 |
| Task 4 (sparse async) | 1 | 3 (new file) | 7 |
| Task 5 (synth limiter) | 1 | 1 | 0 |
| Task 6 (reclassify) | 3 | 2 | 0 |

The two highest-leverage new tests are:
- **`test_fetch_sector_siblings_excludes_tier1_ids`** — catches the tier-1 id false-match correctness bug before it ships.
- **`test_fetch_sector_siblings_uses_partial_index`** — guards against a future schema edit silently degrading the index back to a full table scan.

Do not skip either.

## Verification

After every task:

```
.venv/bin/python -m pytest tests/ --no-header -q
```

After Task 6: full suite via `just test`, `just lint`, `just typecheck` before declaring done.

Manual smoke (both query and ingest paths):

```
just ingest                # should complete faster; event loop responsive
just query "your pitch here"
```

Confirm: report still renders, top-risks still appear, no new errors in trace.

---

## Out of scope (do NOT bundle)

- **Reconcile streaming + batched `has_chunks`** (audit finding #3). Operational/manual path; not on the hot ingest loop. Belongs in a separate plan once a real big-corpus reconcile is needed.
- **Connection reuse / pooling for `MergeJournal`** (audit bonus: 90 PRAGMA executions per query on alias fetch). The "one short-lived connection per call, no pool" pattern is documented in `merge.py` lines 1-7 as deliberate. Changing it is a wider refactor touching every `_*_sync` method.
- **Rerank candidate-summary length cap** (audit finding #8). Already flagged as `TODO(scaling)` at `llm_rerank.py:75-80`. Leave for the maintainer.
- **Tier-3 wasted-signal embed** (`entity_resolution.py:652` — `embed_text_existing = f"{sibling}\n"` embeds the synthetic key string, not real entity content). Correctness/efficacy concern, not a perf bottleneck.
- **`asyncio.gather` → `anyio.create_task_group` migration**. Project-wide refactor; out of scope here.
- **Renaming `Corpus` → `IngestCorpus`** (`docs/plans/2026-05-01-cleanup-findings.md` Fix 3). Use whichever name is current at execution time.
- **Bumping `ingest_concurrency` default** now that sparse no longer stalls the loop. Tune empirically in a separate change once Task 4 ships.

## Risk

- **Task 1 Step 2 + Task 2 Step 4 (partial expression index)** — both the index's `WHERE` predicate and the query's `WHERE` predicate must contain `instr(canonical_id, '::') > 0` for two reasons:
  1. **Correctness** — without it, tier-1 ids (registrable domains, no `::`) get falsely matched against sectors via `substr(id, 2)`. Step 5's `test_fetch_sector_siblings_excludes_tier1_ids` is the regression guard.
  2. **Planner** — SQLite's partial-index planner only picks `merge_sector_idx` when the query's WHERE clause is provably a subset of the index's WHERE clause. Drop the guard from either side and the planner falls back to a full scan. Step 5's `EXPLAIN QUERY PLAN` test catches this.

  If the planner still skips the index for any reason, the fallback is a `STORED` generated column (`ALTER TABLE merge_journal ADD COLUMN sector_suffix TEXT GENERATED ALWAYS AS (CASE WHEN instr(canonical_id, '::') > 0 THEN substr(canonical_id, instr(canonical_id, '::') + 2) ELSE NULL END) STORED` + non-partial index on `(sector_suffix, merge_state) WHERE sector_suffix IS NOT NULL`). STORED requires no migration script for new rows; existing rows backfill via a one-time `UPDATE` before the index is created.
- **Task 3** — `qdrant-client.upsert(points=[...])` already accepts a list, but the wire payload size grows. For docs with hundreds of chunks, consider chunking the upsert into batches of 100 if Qdrant returns a payload-too-large error. Current real corpus has ~10 chunks/doc; not a near-term risk. Net atomicity actually **improves** — Qdrant treats a multi-point upsert as one atomic op, so per-doc partial failures (some chunks land, some don't) become impossible.
- **Task 4 Step 2** — the `SparseEncoder` type alias may be referenced by tests with sync-callable fakes (`rg -n "sparse_encoder\|SparseEncoder" tests/` before merging). Update fakes in lockstep; do not introduce a sync/async branch in production code.
- **Task 5** — none. Pure defensive guard; default value matches today's effective behavior.
- **Task 6** — none beyond standard async-conversion mechanics.

Everything else is index-add or query-rewrite with idempotent `IF NOT EXISTS` schema clauses. Rollback is `git revert` — no destructive operations, no data migrations.

## Memory / preferences

- **Sequential, one-task-at-a-time execution.** Do not dispatch parallel agents.
- **No commits or staging from agents.** Parent owns commits — agent briefs must explicitly forbid `git add` / `git commit`.
- **Stay within the explicit CREATE/MODIFY/DELETE list per task.** No tangential dep bumps, refactors, or "small wins" — cite the Task 10 pytest-cov overstep as the pattern to avoid.
- **Specialty agents over general-purpose** when delegating (`python-development:python-pro` for Python work).
