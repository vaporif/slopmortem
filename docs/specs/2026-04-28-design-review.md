# slopmortem design review — open issues

**Date:** 2026-04-28
**Status:** post obvious-fix pass — only items needing discussion remain (4 open)
**Spec under review:** `docs/specs/2026-04-27-slopmortem-design.md`
**Original review source:** 10 parallel ultrathink agents, one per dimension (API, Security, Concurrency, Data integrity, Cost, Retrieval, Entity resolution, Testing, Observability, Architecture).

The original review had 34 numbered findings + LOW polish items. Mechanical fixes plus #1 (FACET_BOOST calibration), #2 (OpenRouter Protocol scope — resolved by switching v1 to OpenRouter), and #6 (entity-resolution flip GC — resolved via reverse-index + reconcile drift class (f)) have been applied to the spec; the items below remain because each carries a real design tradeoff or empirical question.

---

## Open issues

### MED

**#29 — `SourceAdapter` Protocol is too vacuous** [Architecture #10, spec §sources]
Curated YAML, HN Algolia, CSV, Wayback, Tavily have wildly different shapes. One Protocol covering all of them either becomes vacuous (returns `Iterable[Any]`) or accumulates kwargs nobody uses.
**Options to discuss:** split into two Protocols — `Source` (primary, yields `RawEntry`) vs. `Enricher` (takes `RawEntry`, returns enriched `RawEntry` with extra fields). Wayback and Tavily-enrich are clearly enrichers; HN Algolia and curated YAML are clearly sources; Crunchbase CSV could go either way. Modest refactor; touches `corpus/sources/base.py` and the ingest orchestration.
**Recommendation:** do the split before Task #4a starts so adapters are written against the right shape.

**#31 — Task #4b (300–500 hand-curated URLs) blocks production utility** [Architecture #9, spec §Tasks]
Curating 300–500 hand-vetted URLs with sector matrix, provenance, CODEOWNERS is owned by the user and has no fallback. Until it ships, the corpus is the ~20-URL test fixture.
**Options to discuss:** (a) ship a "v0 minimum" of ~50 URLs (5/sector across 10 sectors) as part of Task #4a's fixture; gate the full pipeline behind `--allow-thin-corpus` until the production list lands; (b) treat #4b as a scale-up not a blocker; (c) accept the gap.
**Recommendation:** (a) — unblocks end-to-end smoke testing of the full pipeline before #4b finishes.

**#33 — OWASP LLM Top-10 (2025) coverage gaps** [Security F9]
LLM07 System Prompt Leakage NOT addressed. LLM08 Vector & Embedding Weaknesses NOT addressed. LLM10 Unbounded Consumption PARTIAL (no token-bomb DoS protection — a hostile corpus doc could be 50K+ tokens and explode synthesis input cost).
**Options to discuss:** v1 scope question. Minimum viable additions: (a) explicit length cap on retrieved corpus body before inlining (e.g. 50K tokens, hard truncate with span event); (b) LLM07 mitigation by minimizing system prompt content (already partially done); (c) LLM08 — retrieval-side filter on doc length and slop_score is partial coverage; full mitigation needs adversarial embedding tests, deferable to v2.
**Recommendation:** (a) is cheap and worth doing now; (b) requires no work; (c) defer to v2 hardening list.

**#34 — `reliability_rank_version` forces full re-merge** [Architecture #11, spec §skip_key]
Bumping `reliability_rank_version` invalidates *every* skip_key, forcing re-merge of the whole corpus. But rank changes only re-order sections — if the resulting `combined_text` is byte-identical, all derivations (facets, embeddings, summaries, chunks) are identical too.
**Options to discuss:** split skip_key into two layers: `derivation_skip_key` (rank-independent: `sha256(combined_text)` plus prompt/model hashes) and `merge_skip_key` (rank-aware: includes `reliability_rank_version`). When rank bumps, recompute `combined_text`; if its sha256 unchanged, skip all derivation work and only update the rank version.
**Recommendation:** worth doing — saves real cost on rank bumps. Modest journal-schema change.

---

## Cross-cutting themes (reduced)

The spec is internally consistent and unusually explicit. After the obvious-fix pass, one class of weakness remains:

- **Cross-store consistency under config edits** — the entity-resolution flip (#6) and the rank-version skip-key (#34) are the same shape: a config-edit that should re-do *some* work but currently re-does too much (or too little). Same fix pattern: split the cache key into the parts that genuinely changed vs. the parts that didn't.

---

## What was already fixed (traceability)

Applied to spec, no longer in this doc:

**This pass:** #26 (curated drift → quarantine_journal w/ `quarantine_reason="curated_drift"`, non-zero exit, `--accept-corpus-drift` override).


**Critical (8):** #1 FACET_BOOST calibration (provisional 0.01 + sweep eval); #2 OpenRouter scope (resolved by making OpenRouter the v1 LLMClient and dropping Batches); #3 web.archive.org allowlist; #4 HTML injection sanitizer; #5 SSRF guard; #6 entity-resolution flip GC (reverse-index + resolver_flipped state + reconcile drift class (f)); #7 quarantine schema; #8 Pydantic auto-capture leak.
**High (9):** #9 Budget race (reserve/settle per API roundtrip under `Budget.lock`, pessimistic upper bound = prompt_tokens × input_price + max_completion_tokens × output_price, settle to actual on response); #10 SQLite blocks event loop; #11 cache-warm race assertion; #14 tool-use loop branch tests; #15 re-merge delete+upsert atomicity; #16 platform blocklist additions; #17 alias-graph dedup at retrieval; #18 recency Branch C; #19 `getaddrinfo` over `gethostbyname`.
**Med (10):** #20 Range claim correction; #21 cost arithmetic + budget bump to $2; #22 skip_key adds chunk_strategy_version + taxonomy_version; #23 canonical front-matter; #24 default `CapacityLimiter`; #25 batch_id orphan persistence; #27 SpanEvent registry; #28 unified structured-outputs API; #30 stage progress to stderr; #32 Task #9 folded into G1.
**Low (8):** prompt_sha split; cache_control TTL drift note; safe_path regex validator; cassette regex additions (JWT, Stripe, batch IDs); render moved to top level; test_mcp.py removed; cassette-miss meta-test; JWT/Stripe scrubber.
