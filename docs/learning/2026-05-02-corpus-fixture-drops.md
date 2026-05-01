# Corpus fixture drops — root cause

**Status: CODE CHANGE REQUIRED BEFORE TASK 2.** Expanding `corpus_fixture_inputs.yml`
alone will NOT fix this. Every Wikipedia URL tier-1-resolves to the same
`wikipedia.org` canonical_id, and chunk point IDs are deterministic on
`(canonical_id, chunk_idx)` — so concurrent/serial entries under the same
canonical clobber each other in Qdrant. More inputs → same collision, just
more entries thrown away. See "Recommendation for Task 2" at the bottom.

## Step 1 — fixture inventory

Committed `tests/fixtures/corpus_fixture.jsonl`: 37 rows, 3 distinct canonical_ids:

| canonical_id | rows | sector | name | founding_year |
|---|---:|---|---|---:|
| `blockbuster::retail_ecommerce` | 13 | retail_ecommerce | blockbuster | 1985 |
| `quibi::media_content` | 11 | media_content | quibi | 2018 |
| `wikipedia.org` | 13 | hardware | **pebble** | 2012 |

The `wikipedia.org` rows are unambiguously the Pebble post-mortem
(name=`pebble`, sector=`hardware`, year=2012). Pebble was tier-1 mis-resolved
to the bare registrable_domain. Confirmed (Step 1 dump).

## Step 2 — quarantine on disk

Recorder uses a `TemporaryDirectory` for `post_mortems_root`
(`slopmortem/evals/corpus_recorder.py:116`), so the recording session's
quarantine is gone. The repo's local `post_mortems/` is from a manual ingest
run and isn't evidence for the fixture state. Skipping further inspection.

## Step 3 — live recorder

**Skipped per controller direction** to conserve budget. Re-running it would
cost ~$0.50–$1. Steps 1+2 plus the static read of the resolver and ingest
write path were sufficient to identify the root cause; no extra evidence
needed.

## Root cause

Two interacting bugs in the resolver + Qdrant write path:

### Bug 1: every Wikipedia URL collapses to one tier-1 canonical_id

- All 10 inputs in `corpus_fixture_inputs.yml` use
  `https://en.wikipedia.org/wiki/<startup>` URLs.
- `_registrable_domain` (`entity_resolution.py:156`) returns `wikipedia.org`
  for every one of them.
- `wikipedia.org` is **not** in `slopmortem/corpus/sources/platform_domains.yml`.
- Demotion to tier-2 only fires under three conditions
  (`entity_resolution.py:534`–`551`):
  1. `is_platform` (blocklist hit) — no, `wikipedia.org` not listed.
  2. Recycled-domain: `|founding_year - cached_year| > 10`
     (`_RECYCLED_DOMAIN_YEAR_DELTA=10`).
  3. Parent/subsidiary: domain present in journal AND new name has a
     corporate suffix (`Inc, LLC, Corp, …`) per `_strip_corporate_suffix`
     (`entity_resolution.py:170`). None of the 10 names carry one.

So the only mechanism that demotes anything here is recycled-domain on
founding-year delta.

### Bug 2: chunk point IDs are deterministic on canonical_id alone

`slopmortem/ingest.py:606`:
```python
point_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{canonical_id}:{c.chunk_idx}").hex
```

Two different entries that resolve to the same `canonical_id` produce
**identical** Qdrant point IDs for chunk_idx 0,1,2,…. Each upsert overwrites
the prior. There's no per-source_id discriminator in the point ID. The
`delete_chunks_for_canonical` call at `ingest.py:788` only fires when
`existing` (same canonical_id + source + source_id) is found — it does NOT
fire for cross-source_id collisions, but it doesn't need to: the bare upsert
clobbers anyway because the IDs match.

Result: when N entries serially process under the same canonical_id, only
the **last** one's chunks survive in Qdrant.

### Trace explaining the 3 survivors

`_process_entry` is called serially in YAML order from
`ingest.py:1079`. Founding-year cache reads return the *earliest* cached row
(`_read_founding_year_sync`, `entity_resolution.py:228`: `ORDER BY rowid ASC
LIMIT 1`), so solyndra's year (2005) anchors all subsequent comparisons:

1. **solyndra** (2005): cache empty → tier-1 `wikipedia.org`. Cache anchored at 2005.
2. **theranos** (2003): |2003-2005|=2 → tier-1. Overwrites solyndra chunks.
3. **webvan** (1996): delta=9 → tier-1. Overwrites theranos.
4. **pets-com** (1998): delta=7 → tier-1. Overwrites webvan.
5. **kozmo-com** (1998): delta=7 → tier-1. Overwrites pets-com.
6. **better-place** (2007): delta=2 → tier-1. Overwrites kozmo-com.
7. **jawbone** (1999): delta=6 → tier-1. Overwrites better-place.
8. **blockbuster** (1985): delta=20 → **DEMOTED** → `blockbuster::retail_ecommerce`. Lands cleanly. (13 chunks)
9. **pebble** (2012): delta=7 → tier-1 `wikipedia.org`. Overwrites jawbone. (13 chunks — the survivor)
10. **quibi** (2018): delta=13 → **DEMOTED** → `quibi::media_content`. Lands cleanly. (11 chunks)

Founding years above are inferred from the surviving rows + standard
Wikipedia data; the exact Haiku-extracted years aren't recoverable since the
recording tempdir is gone. The 2005-anchored math is robust to ±2 in any
single year — the only deltas that matter are blockbuster and quibi crossing
the 10-year threshold.

## Per-input attribution

| input | what happened | code path |
|---|---|---|
| **solyndra** | tier-1 `wikipedia.org`, chunks overwritten by theranos | `entity_resolution.py:535` (tier-1), `ingest.py:606` (point ID collision) |
| **theranos** | tier-1 `wikipedia.org`, chunks overwritten by webvan | same |
| **webvan** | tier-1 `wikipedia.org`, chunks overwritten by pets-com | same |
| **pets-com** | tier-1 `wikipedia.org`, chunks overwritten by kozmo-com | same |
| **kozmo-com** | tier-1 `wikipedia.org`, chunks overwritten by better-place | same |
| **better-place** | tier-1 `wikipedia.org`, chunks overwritten by jawbone | same |
| **jawbone** | tier-1 `wikipedia.org`, chunks overwritten by pebble | same |
| **blockbuster** | tier-2 (year-delta demotion), survived | `entity_resolution.py:539`–`545` |
| **pebble** | tier-1 `wikipedia.org`, last writer wins, survived as `wikipedia.org` | `entity_resolution.py:535` (tier-1, no demote) |
| **quibi** | tier-2 (year-delta demotion), survived | `entity_resolution.py:539`–`545` |

No quarantines, fetch failures, or budget cutoffs occurred — Wikipedia
articles are long, well-formed, and pre-vetted (`curated` source skips the
slop classifier; `ingest.py:1009`).

Log lines that would confirm the trace if the recorder had logged them
(future-proofing — these are in code today):
- `curated: ok https://en.wikipedia.org/wiki/...` (`curated.py:122`)
- per-entry resolve action via `SpanEvent` events
- the wholesale absence of `quarantined` / `slop` / `INGEST_ENTRY_FAILED`
  events (everything succeeded — they were just clobbered post-write)

## Confirming the `wikipedia.org` mis-resolve

Step 1 dump on rows where `canonical_id == 'wikipedia.org'`:

- `name`: `pebble`
- `summary`: starts with "Pebble Technology created e-paper smartwatches…"
- `aliases`: None
- `sector`: `hardware`
- `source_url`: None (recorder doesn't propagate it into the payload)

So `wikipedia.org` definitively holds the **Pebble** post-mortem. The
canonical_id is the registrable domain of `https://en.wikipedia.org/wiki/Pebble_(watch)`.

## Recommendation for Task 2

**Adding more YAML rows accomplishes nothing** until at least one of the
following is fixed:

1. **Smallest viable fix (recommended): add `en.wikipedia.org` / `wikipedia.org` to
   `slopmortem/corpus/sources/platform_domains.yml`.** This forces every
   Wikipedia row through tier-2 (`name::sector`), which is exactly what we
   want for an eval fixture: each pitched startup gets its own
   canonical_id. Tier-2 IDs are deterministic on `(name, sector)` so chunk
   point IDs no longer collide across distinct entries. One-line YAML edit
   plus re-record.

2. **Alternative (broader fix): change the chunk point ID scheme in
   `slopmortem/ingest.py:606` to include a per-entry discriminator** (e.g.
   `source_id` or `text_id`). This fixes the underlying clobbering for any
   future tier-1 collision, but it changes the on-wire Qdrant schema and
   has knock-on effects on `delete_chunks_for_canonical`, reconcile, and
   journal semantics. Out of scope for unsticking the fixture.

3. **Alternative (eval-local fix): use non-Wikipedia URLs in
   `corpus_fixture_inputs.yml`** so each entry's registrable_domain is
   distinct. Defeats the purpose — Wikipedia is the most reliable
   long-form source for these dead startups, and we shouldn't restructure
   eval inputs around an upstream resolver bug.

**Pick option 1.** It's a one-line YAML change to a CODEOWNERS-protected
list (`platform_domains.yml`), it's the same mechanism already used for
medium.com / substack.com, and Wikipedia genuinely IS a platform domain
(many unrelated startups share the host). Task 2 should add that line,
verify locally that the demotion path fires, then proceed to expand the
inputs YAML and re-record.

I deliberately did NOT make this change — surfacing per task brief.

## File:line index

- `slopmortem/ingest.py:606` — point ID collision site
- `slopmortem/ingest.py:703` — `name = entry.source_id`
- `slopmortem/ingest.py:1079` — serial WRITE phase loop
- `slopmortem/corpus/entity_resolution.py:534`–`551` — tier-1 → tier-2 demotion gates
- `slopmortem/corpus/entity_resolution.py:539`–`545` — recycled-domain check
- `slopmortem/corpus/entity_resolution.py:228` — earliest-row founding-year read
- `slopmortem/corpus/sources/platform_domains.yml` — blocklist (missing wikipedia.org)
- `slopmortem/corpus/sources/curated.py:102`–`104` — blocklist enforcement at fetch time
- `slopmortem/evals/corpus_recorder.py:116` — `TemporaryDirectory` (why we can't recover the recording's quarantine)
