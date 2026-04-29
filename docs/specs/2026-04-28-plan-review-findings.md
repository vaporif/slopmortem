# Plan Review Findings — `2026-04-28-slopmortem-implementation.md`

Reviewed: 2026-04-28 by 5 parallel reviewers + 1 confirmer + 1 cross-cutting reviewer.

Plan under review: `docs/plans/2026-04-28-slopmortem-implementation.md`

Severity scale:
- **BLOCKER**: plan cannot be executed as written; will fail or contradict load-bearing spec.
- **CRITICAL**: material rework, data corruption risk, missed acceptance criteria, broken contracts.

All issues below cleared a cross-check by an independent verification agent. Refuted and downgraded findings are recorded at the bottom for transparency.

---

## A. Auto-fixable (obvious mechanical fix, no design decision)

### A1 [BLOCKER] `cache_control` placed at message-dict level instead of inside content blocks
- **Where:** plan.md:1265–1272 (`OpenRouterClient._build_messages`)
- **Now:** `block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}` attached to `{"role": ..., "content": str}`.
- **Fix:** Use Anthropic content-block shape:
  ```python
  block = {"role": "system", "content": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral", "ttl": "1h"}}]}
  ```
  Apply identically to user and assistant messages where `cache=True`.

### A2 [BLOCKER] `prices.yml` missing required `platform_fee_pct: 5.5` key
- **Where:** plan.md:1061–1080
- **Now:** Comment notes the 5.5% fee but no parseable key.
- **Fix:** Add top-level `platform_fee_pct: 5.5` to the YAML so `budget.py` can read it. Reference: `docs/specs/2026-04-28-openrouter-api-corrections.md` Issue 3, Edit 3.2.

### A3 [CRITICAL] Embedding cost off by ×1,000,000 (missing `/1_000_000` divisor)
- **Where:** plan.md:1462 (Step 2b.2)
- **Now:** "accumulate cost from `usage.total_tokens × prices.yml input price`".
- **Fix:** Change formula to `usage.total_tokens / 1_000_000 × price_per_million`. Prices in `prices.yml` are per 1M tokens (see header comment at plan.md:1062).

### A4 [CRITICAL] Render test uses `or` where `and` is required
- **Where:** plan.md:2137
- **Now:** `assert "[" not in md or "](" not in md`, which passes for `[foo][1]` reference-style links.
- **Fix:** Replace with regex check that catches both inline and reference-style:
  ```python
  import re
  assert not re.search(r'\[[^\]]+\]\([^)]+\)', md)  # no inline links
  assert not re.search(r'\[[^\]]+\]\[[^\]]+\]', md)  # no reference-style
  ```

### A5 [BLOCKER] `run_query` test omits required `budget` kwarg
- **Where:** plan.md:2235–2241 (Step 10.1 test) vs plan.md:2256 (Step 10.2 signature)
- **Now:** Test calls `run_query(...)` without `budget=...`; signature requires it as kw-only.
- **Fix:** Add `budget=Budget(cap_usd=1.0)` (or equivalent test fixture) to the test call site.

### A6 [CRITICAL] Curated v0 YAML path inconsistent across plan
- **Where:** plan.md:54, plan.md:1695, plan.md:1750
- **Now:** Three references: `slopmortem/corpus/sources/curated/post_mortems_v0.yml`, `corpus/curated_v0.yml`, `corpus/curated_v0.yml`.
- **Fix:** Pick the path declared in the Task 4a Files block (`slopmortem/corpus/sources/curated/post_mortems_v0.yml`) and update plan.md:54 and plan.md:1750 to match. The Files block is canonical.

### A7 [BLOCKER] `test_tools_schema.py` imports `jsonschema` before dep is added
- **Where:** plan.md:485 (test imports) vs plan.md:519 (`uv add --dev jsonschema` in Step 1.10)
- **Fix:** Move the `uv add --dev jsonschema` instruction into the bootstrap `pyproject.toml` (plan.md:96–142) so the dep is present when the test is first written. Remove the redundant `uv add` from Step 1.10.

### A8 [CRITICAL] `resp.usage` accessed as dict but `openai` SDK returns typed object
- **Where:** plan.md:1226–1230
- **Now:** `usage.get("cost", 0.0) if isinstance(usage, dict) else 0.0`; the typed object falls through to `0.0`.
- **Fix:** Use attribute access on the typed object:
  ```python
  usage = resp.usage
  if usage is None:
      cache_read = cache_write = 0
      cost += 0.0
  else:
      ptd = getattr(usage, "prompt_tokens_details", None)
      cache_read += getattr(ptd, "cached_tokens", 0) if ptd else 0
      cache_write += getattr(ptd, "cache_write_tokens", 0) if ptd else 0
      cost += getattr(usage, "cost", 0.0) or 0.0
  ```
  Update test stubs at plan.md:1163 to construct `CompletionUsage`-like objects (e.g., `SimpleNamespace`) instead of plain dicts so tests catch this regression.

### A9 [CRITICAL] `extra_body={"provider": {"require_parameters": True}}` missing on `llm_rerank` call
- **Where:** plan.md:2050–2062 (Step 7.4) and `LLMClient.complete` Protocol at plan.md:758–768
- **Now:** Protocol has no `extra_body` slot; rerank call passes only `response_format`.
- **Fix:** Add `extra_body: dict[str, Any] | None = None` to the `LLMClient.complete` Protocol and `OpenRouterClient.complete` signature. Pass it through to `chat.completions.create`. Update both `llm_rerank` and `synthesize` call sites to pass `extra_body={"provider": {"require_parameters": True}}`. Reference: `docs/specs/2026-04-28-openrouter-api-corrections.md` Issue 5.

### A10 [CRITICAL] Bootstrap `pyproject.toml` missing required deps
- **Where:** plan.md:96–142 (bootstrap pyproject.toml)
- **Missing:** `tiktoken` (Step 3.9), `jinja2` (Task 0), `readability-lxml` (extraction fallback per spec line 244), `binoculars` (slop classifier per spec line 252).
- **Note:** `anthropic>=0.40` (plan.md:106) is unused in v1 (only `openai` SDK pointed at OpenRouter is used); remove to reduce confusion.
- **Fix:** Add the four missing deps to bootstrap pyproject.toml. Remove `anthropic`. Verify package names with `uv add --dry-run` if uncertain.

### A11 [CRITICAL] `budget_exceeded` field hardcoded `False`, never reflects real breach
- **Where:** plan.md:2289–2291 (`PipelineMeta` construction in `run_query`)
- **Now:** `BudgetExceeded` propagates out of `run_query`, so the only path reaching the return is no-exception, where the field is unconditionally `False`.
- **Fix:** Wrap the pipeline body in `try/except BudgetExceeded` and on catch return a partial `Report` with `pipeline_meta.budget_exceeded=True` plus whatever stages completed. The renderer already shows the field in the footer (spec line 895).

### A12 [BLOCKER] Eval runner has no LLM isolation; runs live on every `make eval`
- **Where:** plan.md:2397–2399 (Step 11.2), plan.md:2419 (Step 11.5), plan.md:158–159 (Makefile target)
- **Now:** Runner calls `pipeline.run_query` with no fake/cassette mechanism; baseline regression detection is non-deterministic + paid.
- **Fix:** Default eval runner to `FakeLLMClient` + `FakeEmbeddingClient` populated from cassettes. Add `--live` flag (or `RUN_LIVE=1` env var) for explicit live runs. Bake cassette generation into a separate `make eval-record` target. Default `make eval` reads from cassettes.

### A13 [CRITICAL] Task 9 (real tool impls) ordered after Task 8 (synthesize); `NotImplementedError` at integration test
- **Where:** plan.md:19–36 (execution order: Task 8 at position 13, Task 9 at position 14)
- **Now:** `synthesis_tools(config)` returns specs wrapping `NotImplementedError` stubs from Task 1; Task 8's tool-use fixture invokes them.
- **Fix:** Move Task 9 to run before Task 8 (swap positions 13 and 14 in the execution table). Update Gate references accordingly.

### A14 [CRITICAL] `summarize.py` listed in spec, but no task creates it
- **Where:** spec.md:369–374, spec.md:498, spec.md:948–950 (component listed); plan has zero matching deliverable.
- **Now:** `payload.summary` is required by `llm_rerank` (plan.md:2033 test asserts it) but ingest never populates it.
- **Fix:** Add a TDD substep to Task 5b (Ingest CLI + orchestration) that creates `slopmortem/corpus/summarize.py` with `summarize_for_rerank(text, llm) -> str` (≤400 tokens), wires it into the ingest data flow between `facet_extract` and `embed_dense`, and writes the result into `payload.summary`. Add a unit test using `FakeLLMClient`.

### A15 [BLOCKER] `LlmRerankResult.ranked` length not enforced
- **Where:** plan.md:2029, plan.md:2057–2061
- **Now:** Spec comment at spec.md:879 says `length == N_synthesize` but no JSON-schema constraint, no post-parse check.
- **Fix:** Add post-parse validation in `llm_rerank`:
  ```python
  parsed = LlmRerankResult.model_validate_json(result.text)
  if len(parsed.ranked) != config.n_synthesize:
      raise RerankLengthError(f"expected {config.n_synthesize}, got {len(parsed.ranked)}")
  ```
  Add `RerankLengthError` to `slopmortem/errors.py`. Add a test with a `FakeLLMClient` that returns the wrong length and asserts the error.

---

## B. Needs human decision (do NOT auto-fix)

### B1 [RESOLVED 2026-04-28] IP-pinning into `Laminar.init` URL, deferred to v2
- **Where:** plan.md:635–650 (Step 1.15) vs spec.md:904 vs review-issues.md appendix #6
- **Decision (2026-04-28):** Deferred to v2. The Laminar SDK does not expose a `http_client` / `transport` parameter (verified against `lmnr-ai/lmnr-python` `src/lmnr/sdk/laminar.py`), so the `safe_get` resolver cannot be bound into the SDK's connection pool without upstream changes. Implementing it in v1 would require either upstreaming the parameter or replacing the OTLP exporter through OTel internals; out of scope.
- **Residual risk accepted:** TOCTOU rebind window between resolve-and-validate at `init_tracing` and the SDK's first connect. On the loopback default the exposure is small; on `LMNR_ALLOW_REMOTE=1` it is real and accepted (deployment trusts its own DNS).
- **Spec edits applied:** spec.md:904 and spec.md:1015 rewritten to drop the false TOCTOU-closed claim and document the v2 deferral + residual window. design-review-issues.md:289–292 already records this decision; plan.md Step 1.15 already matches (resolve-and-validate only, no pin).

### B2 [RESOLVED 2026-04-28] HN Algolia endpoint, pinned to `search_by_date`
- **Where:** plan.md Step 4a.5 + spec.md line 242
- **Decision (2026-04-28):** Endpoint pinned to `https://hn.algolia.com/api/v1/search_by_date` (chronological, newest-first). `/search` (relevance-ranked) is wrong for ongoing obituary coverage; it would re-surface the same long-tail popular threads on every ingest run, undermining incremental coverage of newly-failing companies.
- **Spec edits applied:** spec.md:242 now pins the endpoint URL and documents query params (`tags=story`, `numericFilters=created_at_i>=<since-epoch>`, `page=`). plan.md Step 4a.5 mirrors the URL and adds a unit-test assertion that the constructed URL starts with `…/search_by_date?` so accidental swap to `/search` fails loudly.

### B3 [RESOLVED 2026-04-28] `FormulaQuery` facet keys aligned to singular
- **Where:** plan.md:2020 + design-spec-blockers.md Blocker 7
- **Decision (2026-04-28):** Singular everywhere. `Facets` Pydantic fields, taxonomy.yml top-level keys, Qdrant payload keys (`facets.<name>`), and FormulaQuery iteration variable all use the singular form (`sector`, `business_model`, `customer_type`, `geography`, `monetization`).
- **Where it's pinned:**
  - spec.md:812–825 — `Facets` declared once with singular field names
  - spec.md:1138–1142 — taxonomy.yml top-level keys are singular, with anti-plural comment
  - spec.md:620–633 — FormulaQuery iterates `Facets` field names directly
  - plan.md:362–370 — `test_facets_field_names_singular_match_taxonomy` asserts singular and rejects plural
  - plan.md:940–945 — `test_taxonomy_keys_match_facets_fields` asserts the closed set on disk
  - plan.md:953 — Step 0.2 copies taxonomy.yml verbatim from the spec (singular)
- design-spec-blockers.md Blocker 7 marked RESOLVED in the same pass.

### B4 [RESOLVED 2026-04-28] Latency band `21–63s` (Issue #12)
- **Where:** plan.md:2575 vs design-review-issues.md:37
- **Decision:** Bumped latency band to 40–60s (no Tavily) / 60–90s (with Tavily). Kept Sonnet 4.6 + synthesize output cap to preserve quality on the user-visible synthesis step. Docs-only change; no code/behavior impact.
- **Edits:**
  - spec.md:967–971 — synthesize stage rows updated to ~47 t/s reality (warm-call 12–22s; parallel 15–28s); total band updated to 40–60s / 60–90s; added one-line note explaining the reset.
  - plan.md:2575 — acceptance text now reads `latency 40–90s (40–60s no Tavily, 60–90s with Tavily)`.
  - design-review-issues.md:37 — Issue #12 status row marked resolved.

### B5 [RESOLVED 2026-04-28] Qdrant collection dim hardcoded as `1536`
- **Where:** plan.md Task 2b + Task 3 Step 3.3
- **Decision:** Single source of truth: `EMBED_DIMS: dict[str, int]` map in `slopmortem/llm/openai_embeddings.py` keyed by embedding-model id. `EmbeddingClient.dim` reads it; `ensure_collection(client, name, *, dim)` accepts dim as a kwarg; callers pass `dim=EMBED_DIMS[settings.embed_model_id]`. `ensure_collection` raises `ValueError("dim mismatch …")` if an existing collection has a different dim, which closes the silent-corruption footgun when `embed_model_id` is changed mid-development. (Reframed from "migration story": no live data, no migration; the real concern was two drift-prone hardcodes.)
- **Edits:**
  - plan.md Task 2b — `EMBED_DIMS` map declared in `slopmortem/llm/openai_embeddings.py`; `OpenAIEmbeddingClient` raises at construction on unknown model; new `test_unknown_model_raises`; existing dim assertion now reads `EMBED_DIMS[c.model]`.
  - plan.md Step 3.2 — added `test_collection_dim_mismatch_raises`; existing test threads `dim=EMBED_DIMS[...]`.
  - plan.md Step 3.3 — `ensure_collection` signature gains `*, dim: int`, hardcoded `1536` removed, mismatch branch added.
  - spec.md:214 — §Architecture EmbeddingClient paragraph documents `EMBED_DIMS` as the single source of truth and the `ensure_collection` mismatch error.

### B6 [RESOLVED 2026-04-28] `merge_state="alias_blocked"` race window
- **Where:** plan.md Step 3.6 + Step 5a.4, spec.md:522–545
- **Decision:** Atomic single-write (precheck pattern), matching the resolver-flip precheck already in spec.md:523–528. Alias detection runs BEFORE any journal write; the row hits the journal exactly once in its terminal classification (`pending` | `resolver_flipped` | `alias_blocked`). The `aliases` insert and the `merge_journal` insert run inside one SQLite transaction, so a crash rolls both back or commits both.
- **Edits:**
  - spec.md:522–545 — data flow now shows BOTH a resolver-flip precheck and an alias precheck before the journal write; the journal-write line documents the three terminal classifications and notes that `resolver_flipped`/`alias_blocked` rows terminate without raw/qdrant work.
  - plan.md Step 5a.4 — renamed "Alias-graph test" → "Alias-graph test (atomic precheck)"; added explicit atomicity contract paragraph plus two new tests (`test_alias_blocked_atomic_no_pending_residue` ensuring no transient `pending` row, `test_alias_blocked_crash_recovery` for the all-or-nothing transaction).
  - plan.md Step 3.5 — added `test_upsert_alias_blocked_atomic` to the `MergeJournal` test set.
  - plan.md Step 3.6 — added a "Terminal-state writers (atomicity contract)" subsection enumerating the three sanctioned non-`complete` writers (`upsert_pending`, `upsert_resolver_flipped`, `upsert_alias_blocked`), with a `BEGIN; … COMMIT;` invariant and a "callers MUST use these methods" rule.

### B7 [RESOLVED 2026-04-29] `MidStreamError` retry path
- **Where:** plan.md:1256–1258 + plan.md:1296–1301
- **Decision needed:** Specify explicitly that `_call_with_retry` (currently a `...` stub) is responsible for inspecting `finish_reason == "error"` chunks and raising `MidStreamError` *inside* the retry loop. The current ambiguity between caller-raise vs wrapper-raise leaves the retry behavior implementer-defined.
- **Resolution (option a, wrapper-raises-inside-loop):** `_call_with_retry` now owns stream consumption: it calls `chat.completions.create(stream=True, **kw)`, drains the stream, and on `finish_reason == "error"` raises `MidStreamError` *before returning*, caught by its own retry loop. Retries apply only when `error.code == "overloaded_error"` (per corrections-doc Issue 2, the only place that code surfaces); other MidStreamError codes are fatal. Caller's `if fr == "error"` branch reduced to an unreachable safety net with a comment noting why it's kept. spec.md:770 was already aligned with this option; plan.md:1280–1286 updated with the explicit contract and retry/fatal taxonomy.

### B8 [RESOLVED 2026-04-29] `facet_extract` strict mode + Optional fields
- **Where:** plan.md Task 6 (`extract_facets`). `Facets` has 5 Optional-default fields (`sub_sector`, `product_type`, `price_point`, `founding_year`, `failure_year`) which Pydantic v2 omits from `required`, breaking OpenAI strict-mode contract.
- **Decision needed:** Apply the corrections-doc Issue 4 probe at startup, OR drop `strict: True` for prompts using Pydantic models with Optional fields, OR rewrite `Facets` to use sentinel values instead of `None`.
- **Resolution (option d, force-required + nullable post-processor):** Added `to_strict_response_schema(model) -> dict` helper in `slopmortem/llm/tools.py` that inlines `$ref`/`$defs`, strips draft metadata, and force-adds every property to `required` while preserving `anyOf:[T,null]` verbatim (OpenAI's documented strict-mode pattern). All three structured-output call sites updated to use the helper (`extract_facets`, `llm_rerank`, `synthesize`); idempotent for `Synthesis` and `LlmRerankResult` (no Optional-default fields), load-bearing for `Facets`. None of the original three options taken: option (a) would only diagnose, (b) sacrifices grammar mode for a problem the schema can fix directly, (c) corrupts prompt semantics and forces the spec to abandon `*_date_unknown` payload booleans. Edits: plan.md Step 1.8 (+2 tests), Step 1.9 (helper added), Step 1.10 (4 passed), Step 6.2 (helper + extra_body), Step 7.4 (helper), Step 8.4 (helper); spec.md:225 (canonical strict-mode statement updated to use helper); spec.md Foundation row (helper added). Optional dev-time probe (Issue 4) preserved as-is, already in spec.md:202.

### B9 [CRITICAL] CODEOWNERS for curated YAML and `--reclassify` CLI flag
- **Where:** spec.md:243, spec.md:252. Both listed as v1 acceptance criteria; no plan task implements them.
- **Decision needed:** Either add tasks for these, or move them to "Out of scope (v1)" with explicit rationale.

---

## C. Refuted findings (for the record)

These were flagged by individual reviewers but did not survive cross-check:

| ID | Reason refuted |
|----|----------------|
| `tracing` IP-pin (Reviewer A version) | See B1 — actually still in conflict; flagged for human review |
| `safe_path` quarantine missing kind tests | Quarantine path is exercised by Task 3 journal tests; coverage gap, not blocker |
| `<untrusted_document>` in `facet_extract` test | Tag lives in the system block by design; framing is intentional |
| `merge_state` enum lacks `alias_blocked` | Blockers-doc corrected enum already includes it; plan matches corrected version |
| `synthesize_all` warm-call hard assert | Step 2.10 describes graceful re-warm + `CACHE_WARM_FAILED` event; not a hard abort |
| `synthesize` tool-loop dispatch missing | Loop lives in `OpenRouterClient.complete` (plan.md:1241–1253), not in the stage |

---

## D. Downgraded findings (FYI, not blockers/criticals)

| ID | Original | Downgraded notes |
|----|----------|------------------|
| `safe_path` quarantine tests | BLOCKER → important | Add a quarantine happy-path test in `test_paths.py` |
| `_call_with_retry` stub ambiguity | BLOCKER → important → RESOLVED | See B7 above (wrapper-raises-inside-loop) |
| `FakeLLMClient` Protocol conformance | CRITICAL → important | Add `@runtime_checkable` + `isinstance(FakeLLMClient(), LLMClient)` assertion |
| SQLite WAL checkpoint pressure | CRITICAL → important | Default `wal_autocheckpoint=1000` bounds growth; consider explicit `PRAGMA wal_autocheckpoint=1000` for clarity |
| `alias_blocked` UNIQUE INDEX | BLOCKER → important | UNIQUE INDEX prevents silent corruption; constraint error surfaces correctly |
| `facet_extract` strict + Optional | BLOCKER → important → RESOLVED | See B8 above (force-required helper applied to all three structured-output call sites) |

---

## E. Coverage gaps from cross-cutting review

Spec items not addressed by any plan task:

1. `slopmortem/corpus/summarize.py` — see A14
2. `binoculars` dependency declaration — see A10
3. `tiktoken` in `pyproject.toml` — see A10
4. `jinja2` in `pyproject.toml` — see A10
5. `readability-lxml` extraction fallback — see A10
6. `slopmortem ingest --reclassify` CLI flag — see B9
7. `.github/CODEOWNERS` for curated YAML — see B9
8. `embed_dense.py` module wrapper. Task 2b creates `openai_embeddings.py` (the client) but not the corpus-side wrapper; verify whether this is a true gap or a naming decision before adding a task.
