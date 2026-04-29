# OpenRouter API corrections

**Date:** 2026-04-28
**Companion to:** [2026-04-27-slopmortem-design.md](2026-04-27-slopmortem-design.md)
**Status:** open — 5 spec edits + 1 implementation guidance note

## Why this exists

The 2026-04-27 design spec switched the v1 LLM transport from `claude -p` subprocess to OpenAI SDK pointed at OpenRouter. The OpenRouter assumptions in that spec were verified against current OpenRouter docs by 5 parallel research agents on 2026-04-28, then re-verified by a sixth fact-check pass. Two assumptions held cleanly. Three need correction. One was never verifiable from public docs and needs a runtime probe.

The corrections are spec-only. No code lands in v1 from this plan, only edits to `docs/specs/2026-04-27-slopmortem-design.md`. A small note also belongs in `prices.yml` (see Issue 3).

## Source-of-truth URLs

These are the OpenRouter doc pages each correction draws from. The fact-check agent confirmed all eight resolve as of 2026-04-28; if a future re-check finds a 404, the OpenRouter docs site has been reorganised and these URLs need re-discovery, not deletion.

- Usage accounting: https://openrouter.ai/docs/use-cases/usage-accounting
- Errors and debugging: https://openrouter.ai/docs/api/reference/errors-and-debugging
- Pricing: https://openrouter.ai/pricing
- API parameters: https://openrouter.ai/docs/api/reference/parameters
- Anthropic-skin (Messages) endpoint: https://openrouter.ai/docs/api/api-reference/anthropic-messages/create-messages
- Claude Code integration (documents `ANTHROPIC_BASE_URL`): https://openrouter.ai/docs/guides/coding-agents/claude-code-integration
- Anthropic model listing: https://openrouter.ai/anthropic
- Structured outputs: https://openrouter.ai/docs/guides/features/structured-outputs

---

## Issue 1: cache-token field names are OpenAI-shape, not Anthropic-shape

**Severity:** must-fix. The spec names upstream fields that don't exist on the OpenRouter response.

The spec assumes the LLMClient extracts cache tokens from "OpenRouter's usage extension" but doesn't name the actual fields. The implementer reading the spec has no way to know what to read off the response.

**Verified facts (from usage-accounting docs):**

```json
"usage": {
  "prompt_tokens": 194,
  "prompt_tokens_details": {
    "cached_tokens": 0,
    "cache_write_tokens": 100,
    "audio_tokens": 0
  },
  "completion_tokens": 2,
  "completion_tokens_details": { "reasoning_tokens": 0 },
  "cost": 0.95,
  "cost_details": { "upstream_inference_cost": 19 },
  "total_tokens": 196
}
```

- `usage.prompt_tokens_details.cached_tokens` = cache reads (hits). Subset of `prompt_tokens`, not additional.
- `usage.prompt_tokens_details.cache_write_tokens` = cache creation. Note the asymmetric naming: the read field is bare `cached_tokens`, the write field has a `_tokens` suffix.
- `usage.cost` = USD cost already net of cache savings. Use this as the source of truth for budget accounting.
- `cost_details.upstream_inference_cost` is BYOK-only and absent on the standard PAYG path.

**Spec edits:**

- [ ] **Edit 1.1 — line 202.** The `CompletionResult` description is fine; internal field names (`cache_read_tokens` / `cache_creation_tokens`) can stay since they are an abstraction. But the line that says they are "populated when the routed backend reports them" should add a parenthetical naming the upstream fields:

  Find: `optional `cache_read_tokens`/`cache_creation_tokens` (best-effort; populated when the routed backend reports them).`

  Replace with: `optional `cache_read_tokens`/`cache_creation_tokens` (best-effort; populated from `usage.prompt_tokens_details.cached_tokens` / `cache_write_tokens` when OpenRouter routes to an Anthropic backend).`

- [ ] **Edit 1.2 — line 203.** The line that describes the extraction is currently vague ("OpenRouter exposes the underlying provider's cache fields under its own usage shape"). Make it concrete.

  Find: `(OpenRouter exposes the underlying provider's cache fields under its own usage shape — the LLMClient extracts and normalizes them into `CompletionResult.cache_read_tokens` / `cache_creation_tokens`)`

  Replace with: `(read from `usage.prompt_tokens_details.cached_tokens` (cache hits, a subset of `prompt_tokens`) and `usage.prompt_tokens_details.cache_write_tokens` (cache creation); the LLMClient normalizes these into `CompletionResult.cache_read_tokens` / `cache_creation_tokens`. Total cost is read from `usage.cost` rather than reconstructed from rates, so cache discount accounting requires no client-side multiplier table.)`

- [ ] **Edit 1.3 — line 712.** The Budget cost-computation line currently says `cost_usd` is computed from `usage.prompt_tokens`, `usage.completion_tokens`, and the cache fields against the per-model price table. With `usage.cost` available, the spec should prefer the OpenRouter-reported cost and treat the price-table calculation as a cross-check, not the primary source.

  Find: `Cost is computed from `usage.prompt_tokens`, `usage.completion_tokens`, plus the `CompletionResult.cache_read_tokens` / `cache_creation_tokens` extracted from OpenRouter's usage extension, against the per-model price table; measured, not estimated.`

  Replace with: `Cost is read from `usage.cost` (OpenRouter-reported, already net of cache savings); the per-model price table in `prices.yml` is used only for the pre-call reservation upper bound and as a sanity-check against `usage.cost` (an alarm fires if they diverge by more than 5%, signalling either a price-table drift or an OpenRouter pricing change). `usage.prompt_tokens`, `usage.completion_tokens`, and the cache-token counts are still recorded on the Laminar span for cache-hit ratio reporting.`

- [ ] **Edit 1.4 — line 942.** The Open-questions item naming the wrong fields.

  Find: ``cache_read_tokens` / `cache_creation_tokens` (extracted from OpenRouter's usage extension)`

  Replace with: ``usage.prompt_tokens_details.cached_tokens` / `cache_write_tokens` (the LLMClient normalizes these into `CompletionResult.cache_read_tokens` / `cache_creation_tokens`)`

- [ ] **Edit 1.5 — line 817.** The cost-table caveat references the cache field. Same rename treatment.

  Find: `concretely measurable from `CompletionResult.cache_read_tokens``

  Replace with: `concretely measurable from `usage.prompt_tokens_details.cached_tokens` (surfaced on `CompletionResult.cache_read_tokens`)`

- [ ] **Edit 1.6 — verify the rename is complete.** After the edits above, run:

  ```
  grep -nE 'cache_creation_input_tokens|cache_read_input_tokens' docs/specs/2026-04-27-slopmortem-design.md
  ```

  Expected: zero matches. If any remain, they are Anthropic-native names that won't appear on the OpenRouter response and need to be replaced with the OpenAI-shape names above.

---

## Issue 2: HTTP 529 is not in OpenRouter's surface; replace the mapping

**Severity:** must-fix. Code written against this assumption will never see a 529 and will silently miss the overload class.

Spec line 711 currently says: *"HTTP 529 (`overloaded_error`) from upstream is mapped to a generic transient retry."*

**Verified facts (from errors-and-debugging docs):**

OpenRouter's documented status codes are exactly: **400, 401, 402, 403, 408, 429, 502, 503**. There is no 529. Anthropic upstream `overloaded_error` (529) most plausibly surfaces as **502** ("model is down or we received an invalid response from it"). 503 is reserved for "no available provider meets routing requirements" and is fatal in practice; retrying won't help because the routing pool is exhausted.

There is also a third overload pathway the spec doesn't mention: **mid-stream errors arrive as SSE chunks with HTTP 200**, not as a top-level status. A client that branches only on status code will miss them. The chunk carries `finish_reason: "error"` and an `error.code` with the upstream code in `metadata.raw`. The `openai` Python SDK does not raise `RateLimitError` for these.

**Spec edits:**

- [ ] **Edit 2.1 — line 711.** Replace the line wholesale.

  Find: `Rate-limit detection: SDK `RateLimitError` (HTTP 429) is handled by the openai SDK's built-in `Retry-After`-aware backoff (OpenRouter forwards the upstream provider's `Retry-After` header). HTTP 529 (`overloaded_error`) from upstream is mapped to a generic transient retry. After max retries the candidate drops per the rule below.`

  Replace with: `Rate-limit detection: SDK `RateLimitError` (HTTP 429) is handled by the openai SDK's built-in retry path. The SDK reads `Retry-After` / `retry-after-ms` if the response carries them and falls back to exponential backoff with jitter otherwise; OpenRouter does not document whether it forwards the upstream provider's `Retry-After` header, so the fallback path is treated as the steady-state expectation, not a failure mode. Anthropic upstream overloads (`overloaded_error`, native HTTP 529) surface on the OpenRouter path as one of two shapes: (a) HTTP **502** pre-stream (mapped to a transient retry alongside other 5xx), or (b) a mid-stream SSE chunk at HTTP 200 with `finish_reason: "error"` and `error.code` carrying the upstream code in `metadata.raw`. The LLMClient inspects every streamed final chunk for the error finish-reason and re-raises a synthetic transient exception so the same retry path applies. HTTP **503** ("no available provider meets routing requirements") is treated as fatal; retrying does not change the routing pool. HTTP **402** (insufficient credits) is fatal and short-circuits the budget loop. After max retries the candidate drops per the rule below.`

- [ ] **Edit 2.2 — verify the rename.** After Edit 2.1:

  ```
  grep -nE '529|overloaded_error' docs/specs/2026-04-27-slopmortem-design.md
  ```

  Expected: zero matches outside of the new wording in §Failure handling and any prose paragraphs that explicitly explain the upstream-vs-OpenRouter mapping. If `529` appears anywhere as the asserted OpenRouter status, that's a missed reference.

---

## Issue 3: "Small markup over direct-Anthropic pricing (priced into `prices.yml`)" is wrong

**Severity:** must-fix for cost accuracy; small in dollars.

Spec line 209 currently says: *"routing layer adds one network hop and a small markup over direct-Anthropic pricing (priced into `prices.yml`)"*.

**Verified facts (from pricing page):**

- Verbatim: *"We do not mark up provider pricing. Pricing shown in the model catalog is what you pay which is exactly what you will see on provider's websites."*
- The actual delta vs. direct-Anthropic is a **5.5% platform fee on PAYG credit purchases**, applied at the credit-top-up layer, not as per-token markup.
- BYOK is free for the first 1M requests/month, then 5%.

So `prices.yml` should hold pure Anthropic posted prices (no inflation), and the 5.5% should be modelled separately as a deposit-time fee.

**Spec edits:**

- [ ] **Edit 3.1 — line 209.** Replace the markup phrase.

  Find: `routing layer adds one network hop and a small markup over direct-Anthropic pricing (priced into `prices.yml`)`

  Replace with: `routing layer adds one network hop with no per-token markup over direct-Anthropic pricing (`prices.yml` carries Anthropic's posted rates verbatim); a 5.5% platform fee applies on PAYG credit top-ups and is modelled separately as a deposit-time multiplier on the per-query and per-ingest cost ceilings, not folded into the per-token table`

- [ ] **Edit 3.2 — `prices.yml` (file does not exist yet; this is forward guidance for Task #2 implementer).** When `prices.yml` lands in Task #2 (LLMClient deliverable), it must:

  - Carry Anthropic's posted prices verbatim — no inflation factor.
  - Include a top-level `platform_fee_pct: 5.5` key, consumed by `budget.py` when computing the effective ceiling against `max_cost_usd_per_query` and `max_cost_usd_per_ingest`.
  - Header comment on `platform_fee_pct` documenting that it applies to PAYG credit deposits per OpenRouter's pricing page, not per-token, so it should NOT be folded into the input/output rate columns.

- [ ] **Edit 3.3 — verify per-token rates match Anthropic's site.** After Task #2 lands `prices.yml`:

  ```
  grep -nE 'claude-(sonnet|haiku|opus)' prices.yml
  ```

  Cross-check the listed input/output/cache-write/cache-read rates against `https://www.anthropic.com/api` (or the model card on the Anthropic console). They should match to the cent. If they don't, the `prices.yml` author folded markup in; undo it.

---

## Issue 4: tool-schema pass-through (`anyOf:[T,null]` preservation) is unverifiable from public docs

**Severity:** should-fix. The spec's confident claim is louder than the evidence supports, but the underlying implementation choice is probably right; the fix is hedging the spec, not flipping the implementation.

Spec lines 202 (Architecture) and 321–322 (file structure on `tools.py`) currently claim the `to_openai_input_schema` helper "preserves Pydantic's `anyOf:[T,null]` for Optional fields" because rewriting to `type:[T,null]` "degrades output quality" or "increases invalid-JSON output rates."

**Verified facts (from API parameters page):**

- Verbatim: *"Tool calling parameter, following OpenAI's tool calling request shape. For non-OpenAI providers, it will be transformed accordingly."*
- Anthropic is in the "transformed" bucket, not pass-through.
- OpenRouter does not publish the transformation rules for tool JSON Schemas. There is no doc statement that `anyOf:[T,null]` survives unchanged, AND no statement that it gets rewritten. The claim the spec asserts is unverifiable both ways from public docs.
- Anthropic's own structured-tool docs note that `anyOf` and `type:[T,null]` *both* count toward the union-types budget under strict mode (cap 16), implying the underlying Anthropic API accepts both forms, but say nothing about output-quality differences between them.

The "increases invalid-JSON output rates" claim in the spec is empirical, not doc-derived. It may have come from an earlier `claude -p` measurement that doesn't transfer to the OpenRouter path. Either way, it should not survive into v1 as an unsupported assertion.

**Spec edits:**

- [ ] **Edit 4.1 — line 202.** Soften the load-bearing assertion to a runtime probe + fallback.

  Find: ``Optional[T]` fields preserve Pydantic's `anyOf:[T,null]` shape verbatim. Rewriting to `type:[T,null]` has been observed to *increase* invalid-JSON output rates, so the helper deliberately leaves it alone.`

  Replace with: ``Optional[T]` fields default to Pydantic's `anyOf:[T,null]` emission. OpenRouter explicitly transforms tool schemas before forwarding to non-OpenAI providers (Anthropic among them) and does not publish the transformation rules, so the helper carries a config-driven fallback that can re-emit the field as `type:["T","null"]` if a startup probe shows the `anyOf` shape gets stripped or stringified by the routing layer. The probe is a one-shot tool call with an `Optional[str]` field (issued at LLMClient init when `OPENROUTER_PROBE_TOOL_SCHEMA=1`, default off in production), with `extra_body={"debug": {"echo_upstream_body": true}}` set so OpenRouter returns the body it forwarded; the helper compares the probe response against the original schema and logs which form was preserved. Fallback to the type-array form is not on by default. The assumption is that `anyOf` survives, the probe is the discipline that catches it if not.`

- [ ] **Edit 4.2 — lines 321–322.** Match the architecture wording.

  Find:
  ```
                             #     preserves Pydantic's anyOf:[T,null] for Optional fields
                             #     (rewriting to type:[T,null] degrades output quality).
  ```

  Replace with:
  ```
                             #     emits Pydantic's anyOf:[T,null] for Optional fields by default
                             #     (OpenRouter transforms tool schemas before forwarding to
                             #     Anthropic; pass-through fidelity is unverified from public
                             #     docs, hence the startup probe + type-array fallback noted in
                             #     §Architecture).
  ```

- [ ] **Edit 4.3 — Open questions.** Add a new bullet under "Open questions / future work" tracking the escape hatch.

  After the `**Confirm ingest cache hit rate**` bullet (currently around line 942), add:

  ```
  - **Anthropic-skin endpoint as a tool-schema escape hatch.** OpenRouter exposes a native Anthropic Messages endpoint at `https://openrouter.ai/api/v1/messages` (`ANTHROPIC_BASE_URL=https://openrouter.ai/api`) that bypasses the OpenAI-shape tool-schema transformation entirely. If the startup probe (see §Architecture) shows tool schemas getting rewritten in ways that hurt output quality, switch the synthesis stage to the Anthropic-skin endpoint via `anthropic` SDK targeting OpenRouter; the rest of the pipeline (rerank, facet_extract, summarize) keeps the OpenAI SDK path. Not a v1 deliverable, but the design accommodates it without a rewrite. `LLMClient` is already a Protocol, and a second concrete implementation (`OpenRouterAnthropicClient` over `anthropic` SDK pointed at `https://openrouter.ai/api`) drops in alongside `OpenRouterClient`.
  ```

---

## Issue 5: structured outputs — set `provider.require_parameters: true` defensively

**Severity:** nice-to-have. The flag is documented; the framing the prior research agent gave it ("guards against silent stripping") is editorial, not literally in the docs. The defensive value is real even without the dramatic framing.

OpenRouter's structured-outputs docs do recommend setting `require_parameters: true` in provider preferences when `response_format` is critical to the request. The docs frame this as a procedural compatibility step. If a fallback provider in the routing pool doesn't support `response_format`, OpenRouter without this flag may route to it anyway and the request will succeed but return un-validated JSON (or prose). With the flag, requests are restricted to providers that report supporting the parameter.

**Spec edits:**

- [ ] **Edit 5.1 — line 206.** The synthesis call currently sets `tools` and `response_format` but doesn't show provider preferences.

  Find: `plus `response_format={"type":"json_schema","json_schema":{"name":"Synthesis","schema": Synthesis.model_json_schema(),"strict": True}}` (OpenAI-shape; OpenRouter routes this to Anthropic's grammar-constrained sampling for Sonnet/Haiku).`

  Replace with: `plus `response_format={"type":"json_schema","json_schema":{"name":"Synthesis","schema": Synthesis.model_json_schema(),"strict": True}}` and `extra_body={"provider": {"require_parameters": True}}` (OpenAI-shape `response_format`; the `require_parameters` flag restricts routing to providers that report `response_format` support, so a fallback can't silently downgrade to prompted JSON. OpenRouter routes this to Anthropic's grammar-constrained sampling for Sonnet/Haiku).`

- [ ] **Edit 5.2 — line 651–657.** The rerank call's structured-output block. Same flag.

  Find:
  ```
    response_format={"type":"json_schema",
                     "json_schema":{"name":"LlmRerankResult",
                                    "schema": LlmRerankResult.model_json_schema(),
                                    "strict": True}}.
  ```

  Replace with:
  ```
    response_format={"type":"json_schema",
                     "json_schema":{"name":"LlmRerankResult",
                                    "schema": LlmRerankResult.model_json_schema(),
                                    "strict": True}},
    extra_body={"provider": {"require_parameters": True}}.
  ```

  (Note: this block is inside a multi-line ASCII block; preserve the surrounding indentation when editing.)

- [ ] **Edit 5.3 — verify both call sites.** After 5.1 and 5.2:

  ```
  grep -nE 'require_parameters' docs/specs/2026-04-27-slopmortem-design.md
  ```

  Expected: at least 2 matches (synthesis + rerank). If a future stage adds another `response_format` call, the flag should ride along.

---

## Issue 6: model-id slug format

**Severity:** documentation. Pin them so the implementer doesn't guess.

The OpenRouter model listing uses dot-versioned slugs: `anthropic/claude-sonnet-4.6`, `anthropic/claude-haiku-4.5`. The spec's `prices.yml` example (line 330) uses dash-versioned `claude-haiku-4-5`. The two formats are not interchangeable in API calls.

**Spec edits:**

- [ ] **Edit 6.1 — line 330–331 (prices.yml header comment).** Pin the OpenRouter slugs.

  Find:
  ```
                             #     claude-haiku-4-5        input=$1.00/M  output=$5.00/M
                             #     claude-sonnet-*         see file for current pin
  ```

  Replace with:
  ```
                             #     anthropic/claude-haiku-4.5    input=$1.00/M  output=$5.00/M
                             #     anthropic/claude-sonnet-4.6   see file for current pin
                             #     (slug format follows OpenRouter's catalog at
                             #      https://openrouter.ai/anthropic — dot-versioned, NOT dash)
  ```

- [ ] **Edit 6.2 — line 422.** The config description names the model knobs without giving example values. Add a parenthetical pointing to the slug format.

  Find: `model_facet, model_summarize, model_rerank, model_synthesize,`

  Replace with: `model_facet, model_summarize, model_rerank, model_synthesize (all values are OpenRouter slugs, e.g. `anthropic/claude-sonnet-4.6`, `anthropic/claude-haiku-4.5`),`

---

## Self-review checklist

Run at the end of execution, before marking the plan done.

- [ ] All 6 issues' edits applied to `docs/specs/2026-04-27-slopmortem-design.md`
- [ ] `grep -nE 'cache_creation_input_tokens|cache_read_input_tokens' docs/specs/2026-04-27-slopmortem-design.md` returns zero matches (Issue 1)
- [ ] `grep -nE '529|overloaded_error' docs/specs/2026-04-27-slopmortem-design.md` returns zero matches outside of the new §Failure-handling wording (Issue 2)
- [ ] `grep -nE 'small markup' docs/specs/2026-04-27-slopmortem-design.md` returns zero matches (Issue 3)
- [ ] `grep -nE 'require_parameters' docs/specs/2026-04-27-slopmortem-design.md` returns at least 2 matches (Issue 5)
- [ ] `grep -nE 'anthropic/claude-' docs/specs/2026-04-27-slopmortem-design.md` returns at least 2 matches (Issue 6)
- [ ] No code committed (this plan is docs-only; `prices.yml` is referenced as forward guidance for Task #2 but does not land here)
- [ ] The 5-agent verification report and the 6th fact-check pass are summarized in the commit message so a reviewer can audit the source-of-truth chain without re-running the agents

---

## Out of scope

- Implementing the runtime probe described in Issue 4. The probe is referenced in the spec edit but it lands in Task #2 (LLMClient deliverable) alongside `to_openai_input_schema`. It does NOT land in this docs-only plan.
- Adding the Anthropic-skin (`/api/v1/messages`) implementation. It is referenced in the new Open-questions bullet (Issue 4 Edit 4.3) as a future escape hatch, not a v1 deliverable.
- Rewriting `prices.yml`. The file does not exist in v0; it is a Task #2 deliverable. Issue 3's guidance is forward-looking for that task, not a current edit.
- Re-running the 5-agent verification. Treat the 2026-04-28 verification + fact-check as the source of truth for this plan; if a re-check is wanted later, it lands as a separate plan.
