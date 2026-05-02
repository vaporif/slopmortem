# Corpus Fixture Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or `/team-feature` to implement this plan task-by-task, per the Execution Strategy below. Steps use checkbox (`- [ ]`) syntax — these are **persistent durable state**, not visual decoration. The executor edits the plan file in place: `- [ ]` → `- [x]` the instant a step verifies, before moving on. On resume (new session, crash, takeover), the executor scans existing `- [x]` marks and skips them — these steps are NOT redone. TodoWrite mirrors this state in-session; the plan file is the source of truth across sessions.

**Goal:** Rebuild `tests/fixtures/corpus_fixture.jsonl` so it contains post-mortems that semantically match `tests/evals/datasets/seed.jsonl`. Without this, the eval pipeline's rerank stage scores every candidate well below `min_similarity_score=4.0` and `synthesize`/`consolidate_risks` never run, leaving the eval cassettes structurally incomplete (1 facet + 1 mislabeled rerank cassette per scope, no synthesize, no consolidate).

**Architecture:** Three-step recovery. (1) Diagnose why 7 of the 10 current `corpus_fixture_inputs.yml` entries don't show up in the committed fixture. (2) Expand the inputs YAML to cover every sector represented in `seed.jsonl` with 2–3 known-dead startups each, sourced from Wikipedia. (3) Operator runs `just eval-record-corpus` to regenerate the fixture against the live ingest pipeline (~$2). After this lands, the unblocked Task 2+ in `docs/plans/2026-05-01-eval-runner-cassette-fix.md` can proceed.

**Tech Stack:** Python 3.13, `anyio`, Pydantic v2, the existing ingest pipeline (`slopmortem/ingest.py`), the existing corpus recorder (`slopmortem/evals/corpus_recorder.py`), `just eval-record-corpus` recipe.

## Why this plan exists

`docs/plans/2026-05-01-eval-runner-cassette-fix.md` Task 2 instructs the operator to "re-record every scope" against the live API and expects each scope dir to end up with `≥ 8 files` (facet + rerank + 5 synthesize + consolidate + 2 embed). On 2026-05-02 we discovered that running `just eval` against the existing cassettes produces 0 candidates per row — silently — because:

1. `tests/fixtures/corpus_fixture.jsonl` contains only 3 canonical entities: `blockbuster::retail_ecommerce`, `quibi::media_content`, `wikipedia.org` (Pebble, mis-resolved). All retail/media/hardware.
2. Every pitch in `seed.jsonl` is fintech/healthtech/edtech/devtools/biotech/etc. — no overlap with the fixture.
3. `llm_rerank` scores every candidate 1–2 out of 10. `pipeline._filter_by_min_similarity(threshold=4.0)` drops everything (`slopmortem/pipeline.py:180`).
4. `synthesize` and `consolidate_risks` never run, so no cassettes get recorded for those stages.
5. `synthesize.py:185` (the `except Exception` inside `_run_one`, which `gather_resilient` invokes at line 192) swallows per-candidate exceptions, so even direct cassette misses wouldn't surface as `FAIL` lines.

Re-recording with the existing fixture reproduces the same broken state. The fix is upstream: rebuild the corpus fixture so it actually overlaps with the eval pitches.

## Non-goals

- **Fixing the `_ByModelLLM` dict-key collision** in `slopmortem/evals/recording_helper.py:159-165`. Three stages share `model="anthropic/claude-sonnet-4.6"` (`model_rerank`, `model_synthesize`, `model_consolidate`), so the routing dict only stores one wrapper per model and rerank/consolidate calls get written to `synthesize__*.json` filenames. This is cosmetic — the cassette loader keys on `(template_sha, model, prompt_hash)`, not the filename — and replay still works. Track separately if naming hygiene matters.
- **Changing `min_similarity_score`** in eval mode. Lowering the threshold so degenerate matches survive would mask the underlying mismatch and produce a vacuous baseline.
- **Replacing `seed.jsonl`** with retail/media/hardware pitches that match the existing fixture. Defeats the eval — the seed is the unit of truth for what query types we want to regression-test against.
- **Re-recording the eval cassettes themselves.** That work belongs to `docs/plans/2026-05-01-eval-runner-cassette-fix.md` Task 2 and runs *after* this plan completes.

## Execution Strategy

Subagents (default), sequential dispatch. Each task runs as a fresh agent; the next task starts only after the previous task's review gate passes and the operator gate (Task 3) completes.

Reason: Task 2 acts on the diagnosis from Task 1, Task 4 verifies the artefact Task 3 produces. No parallel batching is possible. Matches the user's standing preference.

## Task Dependency Graph

- Task 1: depends on `none` → first batch (diagnose ingest drops)
- Task 1.5: depends on `Task 1` → second batch (code fix that unsticks ingest; inserted 2026-05-02)
- Task 2: depends on `Task 1.5` → third batch (expand inputs YAML)
- Task 3: depends on `Task 2` → fourth batch (operator gate — regenerate fixture)
- Task 4: depends on `Task 3` → fifth batch (verify coverage)

Each batch runs one task. There is no parallelism in this plan.

## Agent Assignments

- Task 1: Diagnose ingest drops → python-development:python-pro
- Task 1.5: Code fix (platform_domains + curated) → python-development:python-pro
- Task 2: Expand `corpus_fixture_inputs.yml` → python-development:python-pro
- Task 3: OPERATOR (regenerate fixture) → human
- Task 4: Verify fixture coverage → python-development:python-pro
- Polish: post-implementation-polish → python-development:python-pro (uniform Python diff)

## Subagent constraints

Per the user's standing preferences:

- No agent stages or commits (`git add`, `git commit`). The parent owns commit authorship.
- No work outside the explicit CREATE/MODIFY file list per task. If an agent finds a "small win" outside its ownership, it stops and reports rather than making the change.
- Sequential dispatch — one agent at a time, with a review gate between each.

---

## Task 1: Diagnose why 7 of 10 inputs were dropped from the existing fixture

**Files:**
- Create: `docs/learning/2026-05-02-corpus-fixture-drops.md` (a short investigation report — what dropped, why, what to do about it)

Do NOT modify any code in this task. This is investigation only. The fix (if any code change is needed) lives in a follow-up that Task 2 may depend on.

`tests/fixtures/corpus_fixture_inputs.yml` lists 10 dead startups. The committed `tests/fixtures/corpus_fixture.jsonl` has 37 rows but only 3 distinct `canonical_id` values (`blockbuster::retail_ecommerce`, `quibi::media_content`, `wikipedia.org`). The other 7 inputs (solyndra, theranos, webvan, pets-com, kozmo-com, better-place, jawbone) either failed to ingest, got slop-classified into quarantine, collided during entity resolution, or were never attempted.

We need to know which before expanding the inputs in Task 2 — if the failure mode is "the fetcher gives up on Wikipedia URLs older than X" then adding more Wikipedia inputs accomplishes nothing.

- [x] **Step 1: Inspect the committed fixture for clues about what was kept**

```bash
python3 -c "
import json, collections
counts = collections.Counter()
canonicals = collections.Counter()
sectors = collections.Counter()
with open('tests/fixtures/corpus_fixture.jsonl') as f:
    for line in f:
        d = json.loads(line)
        p = d.get('payload', {})
        canonicals[p.get('canonical_id', '?')] += 1
        sectors[p.get('facets', {}).get('sector', '?')] += 1
        counts['rows'] += 1
print('rows:', counts['rows'])
print('canonical_ids:', dict(canonicals))
print('sectors:', dict(sectors))
"
```

Note the per-canonical chunk count and the inferred sectors. Record both in the investigation report.

- [x] **Step 2: Look for quarantined post-mortems on disk**

Run:
```bash
ls post_mortems/quarantine/ 2>/dev/null | head -20
```

Note: the corpus recorder uses a `TemporaryDirectory` for `post_mortems_root` (`slopmortem/evals/corpus_recorder.py:116`), so quarantines from the fixture's recording session are gone. But if the operator has run ingest locally (separate from the corpus recorder), the quarantine dir might still hold something useful. Record what's there in the report.

- [x] **Step 3: Re-run the corpus recorder against the existing inputs YAML with verbose logging, on a tmp output**

This is a live API call (~$0.50–1 on the existing 10-input list), so it's a small operator gate. Skip this step if the operator declines and infer from Step 1 alone.

The recorder doesn't call `logging.basicConfig`, so by default only `WARNING+` records surface. That's enough — every drop path of interest (`slopmortem/ingest.py:467,801,835,989,1015,1081,1110`, `slopmortem/corpus/reclassify.py:67,72,77,154,167`) already logs at `WARNING`. Don't bother with `LOG_LEVEL`/`LOGLEVEL` — neither env var is read anywhere in the codebase. If you want `INFO` records too, prepend a `python -c` shim that calls `logging.basicConfig(level=logging.INFO)` before `main()`.

```bash
docker compose up -d qdrant
RUN_LIVE=1 PYTHONUNBUFFERED=1 \
  uv run python -m slopmortem.evals.corpus_recorder \
    --inputs tests/fixtures/corpus_fixture_inputs.yml \
    --out /tmp/corpus_fixture_diag.jsonl \
    --max-cost-usd 1.5 \
    2>&1 | tee /tmp/corpus_fixture_diag.log
```

Then grep the log for the names that didn't end up in the fixture (`solyndra`, `theranos`, `webvan`, `pets-com`, `kozmo-com`, `better-place`, `jawbone`) and for terms like `slop`, `quarantine`, `entity_resolution`, `journal`, `deadletter`, `merge_decision`, `failed`, `error`, `dropped`, `skip`. Capture the matched lines per name in the report.

If the recorder produces a different set of canonical_ids on this run, that itself is data — record what changed.

- [x] **Step 4: Write `docs/learning/2026-05-02-corpus-fixture-drops.md`**

The report should answer:

1. Which of the 10 inputs ended up in the fixture, and which didn't.
2. For each missing input, the most likely cause (slop quarantine, entity-resolution merge into another canonical_id, fetch failure, budget cutoff, journal collision). Cite the log line or code path that supports the inference.
3. What `canonical_id` value `wikipedia.org` came from — almost certainly Pebble, but worth confirming via the row's `name` / `summary` payload field.
4. Whether expanding the inputs list will accomplish anything, or whether a code change is needed first. If the latter, describe the smallest code change that would unstick ingest (do NOT make the change in this task — surface it to the user).

Keep the report ≤ 200 lines. It exists to inform Task 2 and to leave a paper trail next time the fixture drifts.

- [x] **Step 5: Verify the report covers all 7 missing inputs**

Run:
```bash
for n in solyndra theranos webvan pets-com kozmo-com better-place jawbone; do
  grep -q -i "$n" docs/learning/2026-05-02-corpus-fixture-drops.md && echo "$n covered" || echo "$n MISSING"
done
```
Expected: every name prints `covered`.

---

## Task 1.5: Code fix — separate fetch blocklist from tier-1-collapse blocklist

**Inserted 2026-05-02 after Task 1 surfaced a code bug.** Task 1's investigation (`docs/learning/2026-05-02-corpus-fixture-drops.md`) found that all 10 inputs tier-1-resolve to `wikipedia.org`, and `slopmortem/ingest.py:606` derives chunk point IDs as `uuid5("{canonical_id}:{chunk_idx}")` — so 7 of 10 entries clobbered each other in Qdrant. The minimal proper fix has two parts:

1. Add `wikipedia.org` to `slopmortem/corpus/sources/platform_domains.yml`. Forces every Wikipedia URL through tier-2 (`name::sector`) so each startup gets a distinct canonical_id. Same mechanism medium.com / substack.com already use.
2. Remove the platform-blocklist filter from `CuratedSource`. Curated YAML rows are explicitly user-vouched-for inputs; the resolver's tier-2 demotion handles canonical_id correctness. The current curated fetch-time filter is leaky over-application of the same list for a different concern.

**Files:**
- Modify: `slopmortem/corpus/sources/platform_domains.yml`
- Modify: `slopmortem/corpus/sources/curated.py`
- Modify: `tests/sources/test_curated.py` (drop the blocklist-skip assertions; the behaviour is gone)
- Modify: `tests/fixtures/curated_test.yml` (drop the medium.com / substack.com rows that exist only to exercise the removed filter; they would otherwise raise `unexpected URL` in the fake `safe_get`)

Do NOT modify ingest.py, entity_resolution.py, or any other file. The point-ID derivation stays the way it is — canonical_id is the right granularity for chunk replacement once entity resolution is correct.

- [x] **Step 1: Add `wikipedia.org` to platform_domains.yml**

Edit `slopmortem/corpus/sources/platform_domains.yml`. Insert `- wikipedia.org` in the `domains:` list. Pick a stable position (alphabetical or grouped with similarly-shaped hosts). Keep the trailing newline.

Verify:
```bash
uv run python -c "
from slopmortem.corpus.entity_resolution import _PLATFORM_DOMAINS
assert 'wikipedia.org' in _PLATFORM_DOMAINS, _PLATFORM_DOMAINS
print('ok')
"
```
Expected: `ok`.

- [x] **Step 2: Strip the blocklist filter from `CuratedSource`**

Edit `slopmortem/corpus/sources/curated.py`. Remove:
- The `PLATFORM_DOMAINS_YAML` module-level constant (line 40).
- The `_load_platform_domains` function (lines 43-51).
- The `self._blocked_domains = _load_platform_domains()` line in `__init__` (line 83).
- The `if domain in self._blocked_domains: ... continue` block in `fetch` (lines 102-105) — this also makes `_registrable_domain` unused inside `fetch`; if the function is now unused anywhere in the module, remove it too. Don't keep an unused private helper.
- Update the module docstring (lines 1-11) and the `fetch` docstring (line 95) to drop the "blocklist" wording.

Do not change the public API: `CuratedSource(yaml_path, *, user_agent, rps)` keeps its signature.

Verify the file still parses and the public API is intact:
```bash
uv run python -c "
from slopmortem.corpus.sources.curated import CuratedSource
import inspect
sig = inspect.signature(CuratedSource.__init__)
assert list(sig.parameters) == ['self', 'yaml_path', 'user_agent', 'rps'], sig
print('ok')
"
```
Expected: `ok`.

- [x] **Step 3: Update `tests/sources/test_curated.py`**

In `test_curated_yields_long_text_rows`:
- Drop the four assertions that reference medium.com / substack.com URLs (lines 93-99 in the current file).
- Update the docstring at the top (line 1) and the inline comment (line 93 area) so they no longer claim a blocklist exists.

Other tests in the file should continue to pass unchanged.

- [x] **Step 4: Update `tests/fixtures/curated_test.yml`**

Remove the two YAML rows for `https://medium.com/@user/blocked-platform-post` and `https://username.substack.com/p/blocked-platform-post`. They exist only to exercise the deleted filter; with the filter gone, the fake `safe_get` would raise `unexpected URL: ...`. Update the file's leading comment to drop the "platform-domain blocklist" wording.

- [x] **Step 5: Run the curated test file**

```bash
uv run pytest tests/sources/test_curated.py -v
```
Expected: every test PASSES.

- [x] **Step 6: Run entity-resolution tests as a regression check**

Adding `wikipedia.org` to the blocklist should not affect the medium.com-based tier-1 demotion tests, but verify:

```bash
uv run pytest tests/corpus/test_entity_resolution.py -v
```
Expected: every test PASSES. If any test fails referencing wikipedia.org, the fix needs adjustment — surface to the user.

- [x] **Step 7: Lint + typecheck**

```bash
just lint && just typecheck
```
Expected: PASS.

---

## Task 2: Expand `corpus_fixture_inputs.yml` to cover every sector in `seed.jsonl`

**Files:**
- Modify: `tests/fixtures/corpus_fixture_inputs.yml` (expand from 10 entries to ~22)

Do NOT touch any other file. The corpus recorder, slop classifier, ingest pipeline, and seed dataset stay as-is. This is a config-data edit only.

The seed dataset covers 10 distinct sectors. Each needs at least 2 known-dead startups with substantive Wikipedia post-mortems so rerank can find above-threshold matches. The current 10 inputs cluster on retail/media/hardware (driven by the original Task 6 author's interests) and miss every sector the seed actually exercises.

Task 1.5 already addressed the code bug Task 1 surfaced (Wikipedia-host tier-1 collapse). Proceed assuming each Wikipedia URL now demotes to tier-2 cleanly. If `git log` shows Task 1.5 was NOT committed, STOP and surface to user.

- [x] **Step 1: Read the seed dataset and the Task 1 report**

Read:
- `tests/evals/datasets/seed.jsonl` — note each pitch's apparent sector and business model.
- `docs/learning/2026-05-02-corpus-fixture-drops.md` — confirm Task 1 said "expanding inputs is the right move".

Map seed sectors to candidate dead startups. The mapping below is starting guidance; verify each one has a substantive Wikipedia article (≥ 4–5 paragraphs of failure context, not a stub) before adding it. Aim for ≥ 2 per sector. Reuse the existing inputs (solyndra, better-place, theranos, webvan, kozmo-com, pets-com, jawbone, blockbuster, pebble, quibi) — keep all 10.

| Seed sector (pitch)             | Existing fixture inputs that match | Add (Wikipedia URL)                                         |
|---------------------------------|-------------------------------------|-------------------------------------------------------------|
| fintech (ledgermint)            | none                                | `Powa_Technologies`, `Wonga.com`, `Wesabe`                  |
| healthtech (vitalcue)           | theranos                            | `Outcome_Health`, `uBiome`                                  |
| climate/energy (gridspring)     | solyndra, better-place              | `A123_Systems`, `Aquion_Energy`                             |
| marketplace/social (kakikaki)   | none                                | `Friendster`, `Path_(social_network)`                       |
| devtools (kappa-cli)            | none                                | `RethinkDB`, `Parse_(platform)`                             |
| edtech (yume-tutor)             | none                                | `AltSchool`, `Knewton`                                      |
| biotech (helixthread)           | theranos                            | `Insys_Therapeutics`, `UBiome` (URL is case-sensitive — capital `U`) |
| social media (smolpark)         | none                                | `Vine_(service)`, `Yo_(application)`                        |
| logistics (lastmile-iq)         | webvan, kozmo-com                   | `Boo.com`                                                   |
| gaming (shardbright)            | none                                | `38_Studios`, `OnLive`                                      |

If any candidate is alive at the time of writing (check the Wikipedia infobox "Defunct" or "Status" line before adding), skip it and pick another from the same sector. Note skips inline in the YAML as a `# skipped: <reason>` comment so the next person doesn't re-evaluate the same name.

- [x] **Step 2: Edit `tests/fixtures/corpus_fixture_inputs.yml`**

Append the new entries to the existing list (preserve the existing 10). Match the existing schema: `name: <slug>`, `description: <one-line>`, `url: https://en.wikipedia.org/wiki/<Article>`. The `description` field is dropped by the recorder (`slopmortem/evals/corpus_recorder.py:54`) but every existing row has one, so include it for consistency. Group entries by sector with a one-line comment header per sector for human readability.

Example shape:

```yaml
# fintech
- name: powa-technologies
  description: UK mobile-payments unicorn that collapsed in 2016
  url: https://en.wikipedia.org/wiki/Powa_Technologies
- name: wonga
  description: UK payday lender, regulator-driven shutdown
  url: https://en.wikipedia.org/wiki/Wonga.com
- name: wesabe
  description: Personal-finance startup outpaced by Mint
  url: https://en.wikipedia.org/wiki/Wesabe

# healthtech (existing: theranos)
- name: outcome-health
  description: Doctor-office ad network, fraud-driven collapse
  url: https://en.wikipedia.org/wiki/Outcome_Health
...
```

The `name` field is the slug used as the curated source's `startup_name`. Keep slugs lowercase-kebab-case to match the existing convention.

- [x] **Step 3: Validate the YAML parses**

Run:
```bash
uv run python -c "
import yaml
from pathlib import Path
data = yaml.safe_load(Path('tests/fixtures/corpus_fixture_inputs.yml').read_text())
assert isinstance(data, list), 'must be a list of rows'
for i, row in enumerate(data):
    assert isinstance(row, dict), f'row {i} not a mapping: {row!r}'
    assert isinstance(row.get('name'), str) and row['name'], f'row {i} missing name'
    assert isinstance(row.get('url'), str) and row['url'].startswith('https://en.wikipedia.org/'), f'row {i} bad url'
assert 22 <= len(data) <= 30, f'expected 22-30 rows, got {len(data)}'
print(f'{len(data)} rows OK')
"
```
Expected: prints `<N> rows OK` where 22 ≤ N ≤ 30. The script asserts the bound — it fails fast if you under- or over-shoot.

- [x] **Step 4: Spot-check one new URL is reachable and substantive**

Pick one of the new entries (e.g. `Powa_Technologies`). Hit the URL via `curl -sI` and confirm `HTTP/2 200`. Don't fetch the body — that's the corpus recorder's job. We just need to verify the URL isn't a typo.

```bash
curl -sI 'https://en.wikipedia.org/wiki/Powa_Technologies' | head -1
```
Expected: `HTTP/2 200`.

If you get `404`, the article was renamed or deleted; pick another name in the same sector.

---

## Task 3: OPERATOR — regenerate the corpus fixture

This is a manual human-in-the-loop step. No subagent runs. Costs real money — the existing `_DEFAULT_MAX_COST_USD = 1.5` ceiling caps the LLM + embedding spend, but expect ~$1–2 in practice for a 22-input list.

- [ ] **Step 1: Start Qdrant**

Run: `docker compose up -d qdrant`

- [ ] **Step 2: Re-run the corpus recorder**

Run:
```bash
RUN_LIVE=1 uv run python -m slopmortem.evals.corpus_recorder \
  --inputs tests/fixtures/corpus_fixture_inputs.yml \
  --out tests/fixtures/corpus_fixture.jsonl \
  --max-cost-usd 3.0
```

The recipe equivalent (`just eval-record-corpus`) hardcodes the `--max-cost-usd` to whatever its default is — check the recipe before relying on it. The direct invocation above bumps the cap to $3 since the inputs list is roughly 2.2× the size of the original.

Expected: process exits 0, prints `wrote tests/fixtures/corpus_fixture.jsonl (<N> bytes)`. The new file replaces the old one atomically (`os.replace` at `corpus_recorder.py:201`).

- [ ] **Step 3: Spot-check the new fixture**

Run:
```bash
python3 -c "
import json, collections
canonicals = collections.Counter()
sectors = collections.Counter()
with open('tests/fixtures/corpus_fixture.jsonl') as f:
    for line in f:
        d = json.loads(line)
        p = d.get('payload', {})
        canonicals[p.get('canonical_id', '?')] += 1
        sectors[p.get('facets', {}).get('sector', '?')] += 1
print('canonical_ids:', dict(canonicals))
print('sectors:', dict(sectors))
"
```

Expected: at least 12 distinct `canonical_id` values, with sector coverage including (at minimum) fintech, healthtech, climate_energy, edtech, devtools, gaming, social_communication. If any of these is absent, return to Task 2 and pick a different startup for the missing sector — the article you picked may have been quarantined as slop or merged into another canonical entity. The valid sector enum is in `slopmortem/corpus/taxonomy.yml`.

- [ ] **Step 4: Diff-review the fixture**

Run:
```bash
git diff --stat tests/fixtures/corpus_fixture.jsonl
```

Expected: large diff (hundreds of lines added, the original 37 rows mostly replaced). If the file shrunk dramatically, something is wrong — the recorder may have hit budget early and committed a partial fixture.

The fixture is binary-ish JSONL — line-level git diff isn't useful for content review. Trust the row-count + canonical_id check from Step 3.

- [ ] **Step 5: Commit the new fixture and the inputs YAML together**

The fixture and the inputs that produced it must move together. Stage and commit both:

```bash
git add tests/fixtures/corpus_fixture.jsonl tests/fixtures/corpus_fixture_inputs.yml
git commit -m "rebuild corpus fixture for seed coverage"
```

(The parent agent does the actual commit; the operator just confirms the working tree state.)

---

## Task 4: Verify the new fixture covers the seed dataset

**Files:**
- Create: `tests/evals/test_corpus_fixture_coverage.py`

Do NOT modify any other file. This is a coverage smoke test that fails loudly the next time someone bumps the fixture without thinking about whether it still matches the seed.

- [x] **Step 1: Write the coverage test**

Create `tests/evals/test_corpus_fixture_coverage.py`:

```python
"""Coverage check: every seed-dataset sector must have at least one matching corpus entry.

Lightweight invariant — runs in milliseconds, no Qdrant, no LLM. Exists to
flag the next time someone bumps the corpus fixture without realising the
seed dataset still expects matching sectors. If this test fails, either
expand the inputs YAML and re-record, or update the seed.
"""

from __future__ import annotations

import json
from pathlib import Path

# Best-effort sector inference from the seed pitch description. Keep the
# mapping tight — the goal is "is there any sector overlap at all", not a
# full taxonomy classifier. If a description is too generic to infer, leave
# it out of the assertion set.
_SEED_SECTORS: dict[str, str] = {
    "ledgermint": "fintech",
    "vitalcue": "healthtech",
    "gridspring": "climate_energy",
    "kappa-cli": "devtools",
    "yume-tutor": "edtech",
    "helixthread": "biotech",
    "smolpark": "social_communication",
    "lastmile-iq": "logistics_supply_chain",
    "shardbright": "gaming",
    # kakikaki = b2c marketplace; could plausibly map to media_content,
    # social_communication, or retail_ecommerce — too ambiguous to assert on.
}


def _seed_names() -> set[str]:
    out: set[str] = set()
    with Path("tests/evals/datasets/seed.jsonl").open() as f:
        for line in f:
            row = json.loads(line)
            out.add(row["name"])
    return out


def _fixture_sectors() -> set[str]:
    out: set[str] = set()
    with Path("tests/fixtures/corpus_fixture.jsonl").open() as f:
        for line in f:
            row = json.loads(line)
            facets = row.get("payload", {}).get("facets") or {}
            sector = facets.get("sector")
            if isinstance(sector, str):
                out.add(sector)
    return out


def test_seed_dataset_unchanged() -> None:
    """Guard the _SEED_SECTORS map against silent seed-dataset edits."""
    expected = set(_SEED_SECTORS) | {"kakikaki"}
    assert _seed_names() == expected, (
        "seed.jsonl drifted; update _SEED_SECTORS in this test"
    )


def test_every_inferred_sector_has_a_corpus_entry() -> None:
    """Each sector represented by the seed has at least one fixture entry in that sector."""
    fixture_sectors = _fixture_sectors()
    missing = {seed for seed, sector in _SEED_SECTORS.items() if sector not in fixture_sectors}
    assert not missing, (
        f"corpus fixture has no entries for sectors needed by these seed pitches: {sorted(missing)}.\n"
        f"Fixture sectors present: {sorted(fixture_sectors)}.\n"
        f"Re-run Task 2 of docs/plans/2026-05-02-corpus-fixture-rebuild.md to add coverage."
    )
```

- [x] **Step 2: Run the test**

Run:
```bash
uv run pytest tests/evals/test_corpus_fixture_coverage.py -v
```

Expected: both tests PASS.

If `test_every_inferred_sector_has_a_corpus_entry` fails with `missing = ['vitalcue']` (or similar), that sector's chosen Wikipedia article got slop-quarantined or entity-merged during ingest. Return to Task 2, pick a different startup for that sector, then re-run Task 3.

- [x] **Step 3: Smoke-check `just eval` reaches synthesize**

Run:
```bash
docker compose up -d qdrant && just eval 2>&1 | head -40
```

Expected behaviour:
- For most rows, the rerank stage now finds at least one above-threshold match. `synthesize` runs. Cassettes are *missing* (because the eval cassettes haven't been re-recorded against the new fixture yet — that's Task 2 of the parent plan), so `synthesize.py:185` swallows the `NoCannedResponseError` per candidate and the row still ends up at `candidates_count=0`.
- The `REGRESSION row '<x>' candidate 'acme': missing from current run` lines should still appear (baseline is stale).
- What you should *not* see anymore is uniform 0-candidate behaviour driven by `_filter_by_min_similarity` dropping everything. The `min_similarity dropped %d/%d` log line emits at `INFO` (`slopmortem/pipeline.py:158`), but `just eval` runs `python -m slopmortem.evals.runner` which never calls `logging.basicConfig` — so the line is suppressed at the default `WARNING` threshold. Surface it via a `python -c` shim that configures logging before invoking `runner.main`:

```bash
docker compose up -d qdrant
uv run python -c "
import logging, sys
logging.basicConfig(level=logging.INFO)
sys.argv = [
    'runner',
    '--dataset', 'tests/evals/datasets/seed.jsonl',
    '--baseline', 'tests/evals/baseline.json',
]
from slopmortem.evals.runner import main
main()
" 2>&1 | grep -i 'min_similarity'
```

(Match `just eval`'s args from `justfile:28` — bump them in lockstep if the recipe ever changes.)

If every row still drops 100% of its rerank output (e.g., every `min_similarity dropped` line shows `dropped=N/N`), the new fixture didn't actually overlap with the seed in a way the rerank model recognises. Return to Task 2 and try different (more obviously similar) startups.

- [x] **Step 4: Lint + typecheck**

Run: `just typecheck && just lint`
Expected: PASS.

---

## Wrap-up

After Task 4 lands and is committed:

1. `tests/fixtures/corpus_fixture.jsonl` overlaps `tests/evals/datasets/seed.jsonl` on every sector that has a clean enum match.
2. `tests/evals/test_corpus_fixture_coverage.py` guards the invariant.
3. The blocker on `docs/plans/2026-05-01-eval-runner-cassette-fix.md` Task 2 is cleared — the operator can now re-record eval cassettes against a fixture that produces real synth + consolidate calls.
4. Resume the parent plan at its Task 2 (`OPERATOR — re-record eval cassettes + regenerate baseline`) to finish the original work.

Do NOT run the parent plan's Task 2 from inside this plan. Hand back to the user after Task 4 of this plan completes; they decide when to re-record the eval cassettes.
