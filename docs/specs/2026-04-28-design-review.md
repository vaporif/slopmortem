# slopmortem design review — open issues

**Date:** 2026-04-28
**Status:** all open issues resolved (0 open)
**Spec under review:** `docs/specs/2026-04-27-slopmortem-design.md`
**Original review source:** 10 parallel ultrathink agents, one per dimension (API, Security, Concurrency, Data integrity, Cost, Retrieval, Entity resolution, Testing, Observability, Architecture).

The original review had 34 numbered findings + LOW polish items. Mechanical fixes plus #1 (FACET_BOOST calibration), #2 (OpenRouter Protocol scope — resolved by switching v1 to OpenRouter), and #6 (entity-resolution flip GC — resolved via reverse-index + reconcile drift class (f)) have been applied to the spec; the items below remain because each carries a real design tradeoff or empirical question.

---

## What was already fixed (traceability)

Applied to spec, no longer in this doc:

**This pass:** #26 (curated drift → quarantine_journal w/ `quarantine_reason="curated_drift"`, non-zero exit, `--accept-corpus-drift` override); #29 (split `SourceAdapter` into `Source.fetch() -> AsyncIterable[RawEntry]` + `Enricher.enrich(RawEntry) -> RawEntry`; wayback/tavily are Enrichers, curated/hn/crunchbase are Sources); #31 (v0 corpus ~50 URLs added to Task #4a; Task #4b re-framed as scale-up to ≥200, no longer a v1-utility blocker); #33 (LLM10 per-doc inline cap default 50K tokens with `corpus.doc_truncated` span; LLM07 by-construction note; LLM08 partial mitigation noted, full adversarial-embedding tests added to v2 hardening); #34 (skip_key two-tier split deferred to "Open questions / future work" — rank-version bumps are rare, steady-state cost ~$0.10/week makes the optimization not worth the journal-schema change for v1).


**Critical (8):** #1 FACET_BOOST calibration (provisional 0.01 + sweep eval); #2 OpenRouter scope (resolved by making OpenRouter the v1 LLMClient and dropping Batches); #3 web.archive.org allowlist; #4 HTML injection sanitizer; #5 SSRF guard; #6 entity-resolution flip GC (reverse-index + resolver_flipped state + reconcile drift class (f)); #7 quarantine schema; #8 Pydantic auto-capture leak.
**High (9):** #9 Budget race (reserve/settle per API roundtrip under `Budget.lock`, pessimistic upper bound = prompt_tokens × input_price + max_completion_tokens × output_price, settle to actual on response); #10 SQLite blocks event loop; #11 cache-warm race assertion; #14 tool-use loop branch tests; #15 re-merge delete+upsert atomicity; #16 platform blocklist additions; #17 alias-graph dedup at retrieval; #18 recency Branch C; #19 `getaddrinfo` over `gethostbyname`.
**Med (10):** #20 Range claim correction; #21 cost arithmetic + budget bump to $2; #22 skip_key adds chunk_strategy_version + taxonomy_version; #23 canonical front-matter; #24 default `CapacityLimiter`; #25 batch_id orphan persistence; #27 SpanEvent registry; #28 unified structured-outputs API; #30 stage progress to stderr; #32 Task #9 folded into G1.
**Low (8):** prompt_sha split; cache_control TTL drift note; safe_path regex validator; cassette regex additions (JWT, Stripe, batch IDs); render moved to top level; test_mcp.py removed; cassette-miss meta-test; JWT/Stripe scrubber.
