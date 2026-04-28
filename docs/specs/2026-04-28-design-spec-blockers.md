# Design spec — implementation blockers

**Date:** 2026-04-28
**Companion to:** [2026-04-27-slopmortem-design.md](2026-04-27-slopmortem-design.md)
**Status:** open — 8 confirmed blockers, 3 partials, 2 false positives noted for posterity

## Why this exists

Five parallel reviewers swept the design spec across pipeline data-flow, external API usage, internal consistency, concurrency / failure handling, and schema coherence. A sixth agent independently re-verified each finding with file:line evidence and external doc lookups (lmnr-python source, OpenRouter docs). This file is the deduped, verified punch-list.

All edits land in `docs/specs/2026-04-27-slopmortem-design.md`. No code lands from this plan; the underlying behaviors are Task #N implementer responsibilities, but the spec contracts they read must be correct first.

---

## Runtime-fatal blockers

These break the system on the first call if shipped as-is.

### Blocker 1: `Synthesis.similarity` strict-mode incompatibility

**Where:** spec line ~744.
**Problem:** `similarity: dict[str, PerspectiveScore]` is declared on the `Synthesis` Pydantic model used as `response_format` with `strict: True` (line 206). Pydantic v2 emits `dict[str, X]` as `{"type":"object","additionalProperties":{...}}` — open `additionalProperties`. OpenAI/Anthropic strict structured-output mode rejects open `additionalProperties`. The synthesis call fails at first invocation.
**Fix:** Replace `similarity: dict[str, PerspectiveScore]` with a closed BaseModel naming the four perspective keys explicitly:

```python
class SimilarityScores(BaseModel):
    business_model: PerspectiveScore
    market: PerspectiveScore
    gtm: PerspectiveScore
    stage_scale: PerspectiveScore

class Synthesis(BaseModel):
    similarity: SimilarityScores
    ...
```

### Blocker 2: synthesize_all `asyncio.gather` cancels siblings on first exception

**Where:** spec lines ~678–684 (the gather call), contradicting line ~719.
**Problem:** Bare `asyncio.gather(...)` propagates the first exception and cancels in-flight siblings. Line 719 promises *"that candidate drops from rerank/synthesis with a logged warning; the report notes the gap"* — that guarantee is unreachable with the gather as written. One transient failure on any candidate kills the whole report.
**Fix:** Specify `return_exceptions=True` on the gather (or wrap each per-candidate coroutine in a try/except that returns a sentinel `SynthesisFailure` value), and update the prose at 678–684 to name the chosen mechanism. The reporting path at 719 then has to filter for the sentinel/exception type.

### Blocker 3: Laminar `@observe(ignore_inputs=["candidate.payload.body"])` is a no-op

**Where:** spec line 791.
**Problem:** Verified against `lmnr-python` source (`src/lmnr/sdk/utils.py::get_input_from_func_args`): `ignore_inputs` is matched as `k in ignore_inputs` against top-level parameter names from `inspect.signature(func).parameters.keys()` — dotted paths like `"candidate.payload.body"` never match, so the filter never fires. The full `Candidate` (including `payload.body`) flows to the Laminar exporter. The "no `<untrusted_document>` payload reaches Laminar" guarantee is a fiction.
**Fix:** Two viable shapes:
1. `@observe(ignore_inputs=["candidate"])` and re-attach a redacted `candidate_meta` dict via `Laminar.set_span_attributes(...)` inside the function.
2. Build a redacted projection of the candidate inside the function before any work and use that as the only argument that reaches `@observe`.
Update line 791 prose to specify whichever path is chosen and add the regression test the spec already promises (assert no body strings appear in exporter output, not just that the decorator was called).

### Blocker 4: `merge_state="quarantined"` violates the enum and the table layout

**Where:** spec line 472 vs lines 252, 395, 396–399.
**Problem:** Line 395 enumerates `merge_state ∈ {pending, complete, alias_blocked, resolver_flipped}` — `quarantined` is absent. Lines 252 / 396–399 say quarantined docs go to a *separate* `quarantine_journal` table because they have no `canonical_id` and so cannot live in the row-keyed-by-`(canonical_id, source, source_id)` main journal. Line 472's instruction to write `merge_state="quarantined" in journal` violates both.
**Fix:** Rewrite line 472 to say "write a row in `quarantine_journal` keyed on `(content_sha256, source, source_id)`; no `merge_state` column on that row." Leave the main `merge_state` enum as-is.

---

## Contradictions and missing definitions

These don't crash on first call, but force the implementer to invent a contract and ship something that diverges from the spec.

### Blocker 5: Tier-1 canonical_id keying contradicts itself

**Where:** spec line 258 (prose) vs lines 367–369 (file-layout comment).
**Problem:** Line 258 (corrected): *"Tier 1 is `registrable_domain` only, with founding_year used as a separate stored attribute, not a key component. Earlier drafts keyed tier 1 on `(registrable_domain, founding_year // 5)`..."* — that's the bug-fix narrative. Line 368 in the file-layout block still reads `tier 1: (registrable_domain, founding_year//5) with platform blocklist`. An implementer reading `entity_resolution.py`'s docstring will key on the buggy old shape.
**Fix:** Update lines 367–369 to match line 258. The file-layout comment should say `tier 1: registrable_domain only (founding_year stored as attribute, not key); platform blocklist applies`.

### Blocker 6: Ingest payload literal omits required query-side fields

**Where:** spec lines 525–527 (write contract) vs 611–613, 632–635, 648, 791, 252.
**Problem:** The literal payload list is `{canonical_id, chunk_idx, summary, facets, founding_date, failure_date, sources, text_id}`. Reads on the query side require:
- `failure_date_unknown: bool` and `founding_date_unknown: bool` — described in a comment at 610–613 but missing from the literal. Without them, the recency filter at 632–635 falls into the "both unknown" branch C every time → silent recall loss.
- `name`, `provenance`, `slop_score`, and `body` — referenced at 648, 252, 791 respectively, never enumerated in the write contract.

**Fix:** Extend the payload literal at 525–527 to the full set, naming the booleans explicitly so it matches the read sites. The `_unknown` flags are the load-bearing miss; the others are prose-vs-literal inconsistencies the spec should still close.

### Blocker 7: `facets.<name>` payload nesting is unspecified, plural-vs-singular ambiguous — RESOLVED 2026-04-28 (singular)

**Where:** spec line 596 (FormulaQuery boost) vs taxonomy.yml shape (lines 1022–1095) vs `Facets` Pydantic model (referenced but never declared).
**Problem:** The boost iterates `key=f"facets.{name}"`. Taxonomy keys were plural (`sectors:`, `business_models:`, …). The `Facets` model field names were never written out anywhere in the spec, but the prose around facet extraction used singular forms. If `Facets.sector` (singular) is what ingest writes and the boost iterates `name="sectors"` (plural taxonomy key), the filter never matches and the boost is silently dead.
**Resolution (2026-04-28):** Singular wins. Spec edited to (a) declare the `Facets` Pydantic field set in exactly one place (spec.md:812–825) with singular field names, (b) rewrite Appendix A's taxonomy.yml (spec.md:1137–1217) to singular top-level keys with an explicit comment that plural would silently break the boost, (c) leave the FormulaQuery iteration sketch (spec.md:620–633) unchanged — it already iterates `Facets` field names. Plan mirrors: `tests/test_models.py::test_facets_field_names_singular_match_taxonomy` (plan.md:362–370) and `tests/test_taxonomy.py::test_taxonomy_keys_match_facets_fields` (plan.md:940–945) are anti-plural regressions on both sides.

### Blocker 8: Config keys referenced in code but absent from the tunables list

**Where:** Config tunables enumeration at 423–432.
**Problem:** Three keys are read but never declared:
- `config.slop_threshold` at line 470 (default ~0.7 mentioned at 468, key never declared)
- `config.max_doc_tokens` at line 905 (default 50000 mentioned, key never declared)
- Tier-3 calibration band `[0.65, 0.85]` at line 264 — declared "tunable in config" but no key name given anywhere.

If `Config` uses Pydantic with `extra="forbid"` (likely), these are parse-time failures the moment a user puts them in `config.toml`. If `extra="allow"`, they silently fall back to hardcoded defaults.
**Fix:** Add the three keys to the tunables list at 423–432 with their defaults: `slop_threshold: float = 0.7`, `max_doc_tokens: int = 50000`, `tier3_calibration_band: tuple[float, float] = (0.65, 0.85)` (or a chosen name).

### Blocker 9: shared Pydantic models referenced but never declared

**Where:** `models.py` inventory at line 409, plus consumers at 670, 676, 763 (ScoredCandidate); 43, 438, 564, 774, 802, 1013 (InputContext); 652 (`Candidate.alias_canonicals`).
**Problem:** Task #1's deliverable is "all shared models" (Gate-1), but the spec never gives field-level definitions for:
- `ScoredCandidate` — sent as `LlmRerankResult.ranked` element via `response_format` JSON Schema. Without fields, the schema is undefined.
- `InputContext` — read by query/eval/replay paths. Fields like `name`, `description`, `years_filter` are implied by usage at 564 but never declared.
- `Candidate` (and its `alias_canonicals[]` attribute referenced at 652) — used throughout (`payload.sources`, `payload.body`, `summary`) without a declaration.

**Fix:** Add declarations to the §File-structure / `models.py` block (line ~409). For `ScoredCandidate`, decide whether it embeds the full `Candidate` (risk: model re-emits fields, can drift) or just `(candidate_id, perspective_scores, rationales)` and the harness rejoins to the K_retrieve list. For `InputContext`, name the fields with types. For `Candidate.alias_canonicals`, declare it as `list[str] = []` on the `Candidate` model.

### Blocker 10: `ToolSpec` double-homed; `synthesis_tools` factory naming

**Where:** lines 316 and 410 (`ToolSpec` location); lines 678 vs 317, 881, 983 (factory vs constant).
**Problem:**
- `ToolSpec` is listed as belonging to both `slopmortem/llm/tools.py` (316) and `slopmortem/models.py` (410). Two import paths for the same name → either circular import or the implementer guesses wrong.
- Line 678 calls `synthesis_tools(config)` (factory), but lines 317 / 881 / 983 refer to `SYNTHESIS_TOOLS` as a constant. The semantics at 881 ("plus tavily_search/tavily_extract only if `enable_tavily_synthesis`") require a factory; the constant naming is wrong.

**Fix:** Pick one home for `ToolSpec` (most natural: `models.py` since it's a Pydantic-shaped spec consumed across modules; have `tools.py` import from it). Rename `SYNTHESIS_TOOLS` to `synthesis_tools(config: Config) -> list[ToolSpec]` everywhere — it's a factory.

---

## Partial / nuanced (real but narrower than first claimed)

- **Cite line for facet plural-vs-singular:** the original review pointed at line 273 as evidence of singular `sector`; line 273 doesn't actually contain that text. The underlying ambiguity (Blocker 7 above) is real, but specific line citations need to come from wherever `Facets` is declared once that exists.
- **Issue 4 startup probe (`extra_body={"debug":{"echo_upstream_body": true}}` from the corrections doc):** the OpenRouter docs document a debug-echo mechanism but don't pin the exact key name as `echo_upstream_body`. Probe is opt-in (`OPENROUTER_PROBE_TOOL_SCHEMA=1`, off in production), so this is fail-soft, not a runtime blocker. When Task #2 implements the probe, expect to discover the real parameter shape and update the spec.
- **Tool input schemas + strict-mode JSON Schema:** corpus tools are explicitly NOT strict (line 206), so `additionalProperties:false` + all-required is moot for them. If a future stage marks a tool strict, the constraint applies at that point.

## Dropped (false positives, recorded so they don't resurface)

- **`usage.cost` opt-in via `extra_body={"usage":{"include":true}}`:** OpenRouter now returns full usage details automatically on every response. The opt-in flag was the older API. The corrections doc is correct as written.
- **`@observe(ignore_inputs)` on top-level params:** *correctly* filters when the input is a top-level argument; the failure mode is specifically dotted paths (Blocker 3).

---

## Self-review checklist

Run after applying the edits, before marking the plan done.

- [ ] Blockers 1–4 closed with concrete spec edits (no "TBD" placeholders)
- [ ] Blockers 5–10 closed with consistent prose across architecture / file-structure / config sections
- [ ] `grep -nE 'dict\[str, *PerspectiveScore\]' docs/specs/2026-04-27-slopmortem-design.md` returns zero matches (Blocker 1)
- [ ] `grep -nE 'asyncio\.gather' docs/specs/2026-04-27-slopmortem-design.md` matches show `return_exceptions=True` or equivalent at the synthesize_all call site (Blocker 2)
- [ ] `grep -nE 'ignore_inputs=\["candidate\.' docs/specs/2026-04-27-slopmortem-design.md` returns zero matches (Blocker 3)
- [ ] `grep -nE 'merge_state="quarantined"' docs/specs/2026-04-27-slopmortem-design.md` returns zero matches (Blocker 4)
- [ ] `grep -nE 'founding_year *// *5' docs/specs/2026-04-27-slopmortem-design.md` returns zero matches outside the corrected-narrative paragraph (Blocker 5)
- [ ] `Facets` Pydantic field set declared in exactly one place; field names match taxonomy.yml keys and the FormulaQuery iteration (Blocker 7)
- [ ] `slop_threshold`, `max_doc_tokens`, and the tier-3 calibration band are listed in the Config tunables block (Blocker 8)
- [ ] `ScoredCandidate`, `InputContext`, `Candidate` (incl. `alias_canonicals`) have field-level declarations (Blocker 9)
- [ ] `ToolSpec` appears in exactly one module; `synthesis_tools` is named consistently as a factory (Blocker 10)

---

## Out of scope

- Implementing the runtime probe from corrections-doc Issue 4 (Task #2).
- Writing `prices.yml` (Task #2 deliverable; the corrections doc carries forward guidance).
- Adding the v2 escape-hatch implementation against OpenRouter's Anthropic-skin endpoint.
- Re-running the 5-agent verification. Treat the 2026-04-28 verification + 6th-pass fact-check as the source of truth; if a re-check is wanted later, it lands as a separate plan.
