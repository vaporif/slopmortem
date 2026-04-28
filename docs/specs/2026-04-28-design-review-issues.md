# start_slop — design review: open issues

**Date:** 2026-04-28
**Status:** open — to triage and fold into spec
**Companion to:** [2026-04-27-start-slop-design.md](2026-04-27-start-slop-design.md)

Findings from a five-pass technical review of the design spec, re-verified by
parallel cross-checks against the current spec, current SDK code, and current
vendor docs. Issues #1, #2, and #3 are resolved and no longer tracked here
(see spec edits dated 2026-04-28). Original numbering retained so external
references stay stable.

| # | Issue | Severity | Status |
|---|---|---|---|
| 4 | asyncio.Semaphore can't shrink at runtime | blocker | open |
| 5 | Pydantic v2 → Anthropic tool schema rough edges | should-fix | open |
| 6 | DNS-rebinding guard cannot bind to SDK pool | should-fix | open |
| 7 | Async/sync boundary contradicts itself | should-fix | open |
| 9 | Anthropic Batches + prompt caching needs verification | should-fix | open |
| 10 | Mixed cassette stack (vcrpy + respx) | should-fix | open |
| n2 | OpenAI embedding price pin | nit | open |
| n3 | SQLite driver named for journal | nit | open |

**Spec line citations in this document are off by ~30–80 lines** (review was
written against an earlier snapshot). Real locations called out in each
section.

---

## #3 — Per-source vs canonical markdown path is contradictory

**Severity:** blocker — load-bearing for ingest atomicity and synthesis input.

### Problem

Layout (spec:222, spec:335):

```
data/post_mortems/<source>/<text_id>.md
```

`text_id = sha256(canonical_id)[:16]`. So when the same canonical entity is
contributed to by N sources (say `curated`, `hn`, `crunchbase`), there are
N files with the *same* filename in different per-source directories:

```
data/post_mortems/
├── curated/
│   └── ab12cd34.md       ← same canonical_id
├── hn/
│   └── ab12cd34.md       ← same canonical_id
└── crunchbase/
    └── ab12cd34.md       ← same canonical_id
```

On merge, the spec says (spec:402–403):

> write combined_text to `<path>.tmp`, `os.replace`

But `<path>` is parameterized by source. Two readings, both broken:

```
Reading A:                       Reading B:
┌────────────────────────┐     ┌────────────────────────┐
│ curated/ab12cd34.md ───┼─►   │ curated/ab12cd34.md ───┼─► section A only
│   = combined(A,B,C)    │     │ hn/ab12cd34.md ────────┼─► section B only
│ hn/ab12cd34.md ────────┼─►   │ crunchbase/ab12cd34.md ┼─► section C only
│   = combined(A,B,C)    │     │                        │
│ crunchbase/ab12cd34 ───┼─►   │ synthesis must read    │
│   = combined(A,B,C)    │     │ ALL THREE and combine  │
│                        │     │ at query time          │
│ 3× redundant write     │     │                        │
│ journal can drift      │     │ but spec says body is  │
│ which is canonical?    │     │ "INLINED" → from where?│
└────────────────────────┘     └────────────────────────┘
```

Synthesis "INLINES the candidate body by default" (spec:223) — which file
does it load? The spec does not say.

### Why it matters

- Reading A wastes disk and creates N places where the merge_state journal
  can disagree with on-disk content.
- Reading B requires synthesis to enumerate per-source files and re-merge
  at query time, which contradicts the spec's "inline the candidate body"
  language and pushes merge logic into the hot query path.

### Recommendations (pick one)

**Option 1: split per-source raw and merged-canonical paths.**

```
data/post_mortems/
├── raw/
│   ├── curated/ab12cd34.md       ← original section, immutable
│   ├── hn/ab12cd34.md            ← original section, immutable
│   └── crunchbase/ab12cd34.md    ← original section, immutable
└── canonical/
    └── ab12cd34.md               ← merged combined_text, what synthesis loads
```

- Atomic merge writes `canonical/ab12cd34.md` only.
- Per-source files stay frozen; useful for re-merging when reliability_rank
  changes or for forensics.
- Synthesis always reads `canonical/<text_id>.md` — single resolution.

**Option 2: drop per-source files, write only canonical.**

```
data/post_mortems/
└── ab12cd34.md           ← combined_text from all contributing sources
                            front-matter records contributing source list
```

- Simplest; least disk.
- Per-source provenance lives in markdown front-matter / Qdrant payload.
- Re-merge requires re-fetching sources (no cached raw text).

**Recommend Option 1** — costs ~2× disk for the canonical seeding (cheap;
500 docs × ~10 KB × 2 ≈ 10 MB) and gives clear semantics:

| | raw/<source>/<text_id>.md | canonical/<text_id>.md |
|---|---|---|
| Written by | source adapter | merge step |
| Mutable | no (immutable per ingestion) | yes (rewritten on merge) |
| Read by | merge step, reconcile | synthesis, eval |

### Spec edits required

- spec:222 path layout
- spec:335 file-tree comment
- spec:385–406 ingest data-flow steps
- spec:223 synthesis-inlines-body sentence
- spec:222 atomicity sentence
- spec:687–688 atomicity test description
- `safe_path` accepts a `kind ∈ {"raw", "canonical"}` discriminator

---

## #4 — asyncio.Semaphore cannot be "halved at runtime"

**Severity:** blocker — spec promises behavior the stdlib primitive does not
provide.

### Problem

spec:511:

> SDK `RateLimitError` (HTTP 429) or `overloaded_error` (HTTP 529) throttles
> the entire shared semaphore (sleeps `Retry-After` from response headers,
> halves concurrency for the rest of the run).

```
Initial state (N_synthesize-1 = 4):
  Semaphore[█ █ █ █]   ← 4 tokens, 4 slots

Run starts, 4 tasks acquire:
  Semaphore[· · · ·]   task1 task2 task3 task4 in flight
                         │
                         └─► HTTP 429 RateLimitError
                                          │
                                          ▼
            Spec says: "halve concurrency for the rest of the run"
                                          │
                                          ▼
                                ╔══════════════════════╗
                                ║ asyncio.Semaphore    ║
                                ║ has no shrink()      ║
                                ║ no acquire_n()       ║
                                ║ no resize()          ║
                                ╚══════════════════════╝
```

`asyncio.Semaphore` is monotonic — capacity is set at construction and
cannot be reduced. There is no API to "consume" extra tokens to drain
capacity, and even if you `acquire()` extra slots from the rate-limit
handler, the in-flight tasks are still holding their tokens; you'd
deadlock or block forever.

### Recommendation

**Preferred:** use `anyio.CapacityLimiter`, whose `total_tokens` property is
documented as runtime-mutable — assigning a smaller value causes new acquires
to block until in-flight tasks drop below the new cap; assigning a larger
value wakes the appropriate number of waiters. This is exactly the behavior
spec:511 promises and avoids hand-rolling a primitive. anyio is already a
transitive dependency of httpx-via-`AsyncAnthropic`, so no new direct dep.

```python
limiter = anyio.CapacityLimiter(N_synthesize - 1)

# ... on RateLimitError(retry_after=5.0):
limiter.total_tokens = max(1, limiter.total_tokens // 2)
await asyncio.sleep(retry_after)
```

**Fallback** if a stdlib-only solution is required, replace `asyncio.Semaphore`
with a custom counting gate:

```python
class ShrinkableGate:
    """A semaphore-like primitive whose capacity can be reduced."""

    def __init__(self, capacity: int):
        self._capacity = capacity
        self._in_flight = 0
        self._cond = asyncio.Condition()

    async def acquire(self) -> None:
        async with self._cond:
            while self._in_flight >= self._capacity:
                await self._cond.wait()
            self._in_flight += 1

    async def release(self) -> None:
        async with self._cond:
            self._in_flight -= 1
            self._cond.notify()

    async def shrink_to(self, new_capacity: int) -> None:
        """Reduce capacity. New acquires block until in_flight drops below."""
        async with self._cond:
            self._capacity = max(1, new_capacity)
            # waiters that were about to win re-check the loop
            self._cond.notify_all()
```

Behavior on rate limit:

```
on RateLimitError(retry_after=5.0):
    await gate.shrink_to(gate._capacity // 2)
    await asyncio.sleep(retry_after)
    # in-flight tasks complete naturally; new acquires gated at new cap
```

### Spec edits required

- spec:511 — replace `asyncio.Semaphore` with `anyio.CapacityLimiter`
- spec:503 (concurrency section) — note `total_tokens` is mutated on 429/529
- Task #2 (LLMClient) — `anyio` listed as a direct dep
- Task #10 (CLI + pipeline orchestration) — add cap-shrink integration test

---

## #5 — Pydantic v2 → Anthropic tool schema rough edges

**Severity:** should-fix — defensive flattening before sending schemas to
the Anthropic API. Original review framed this as a blocker; verification
against current Anthropic docs did not confirm the failure modes claimed
(`$ref` silently failing, `anyOf:[T,null]` being rejected). The flattening
helper is still worth having to strip Pydantic-specific metadata and to be
robust to future stricter validation, but the severity is should-fix.

### Problem

spec:196:

> argument schemas are auto-derived from a Pydantic arg model attached to
> each tool

Pydantic v2 `model_json_schema()` emits draft-2020-12 with `$defs` / `$ref`
and `anyOf` for `Optional`:

```python
class Facets(BaseModel):
    sector: str
    year: int | None = None
    nested: SomeOtherModel  # ← gets $ref'd
```

```json
{
  "$defs": {
    "Facets": {"type": "object", ...},
    "SomeOtherModel": {"type": "object", ...}
  },
  "properties": {
    "sector": {"type": "string"},
    "year": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    "nested": {"$ref": "#/$defs/SomeOtherModel"}
  },
  "$schema": "https://json-schema.org/draft/2020-12/schema"
}
```

Anthropic's `input_schema` field accepts JSON Schema. Strict-tool-use mode
(opt-in, `"strict": true`) requires `additionalProperties: false` and a
stricter subset; non-strict mode accepts `$ref`/`$defs` and `anyOf:[T,null]`
in practice today. Two pragmatic concerns remain:

- Top-level Pydantic metadata (`$schema`, `$defs`, `$id`) is dead weight in
  the request body and noisy in trace inspections.
- Strict-mode rollout (or future tightening) will reject `$ref` and require
  inlined schemas.

Flattening once at schema-conversion time addresses both without depending
on undocumented validator behavior. The original review's claim that
`anyOf:[T,null]` is non-canonical and must be rewritten is **not supported
by current Anthropic docs**; one engineering writeup found that rewriting to
`"type":["T","null"]` actually *increased* invalid-JSON output rates. Keep
Pydantic's default `anyOf` shape.

### Recommendation

Add an explicit conversion step in `slop/llm/tools.py`:

```python
import jsonref

def to_anthropic_input_schema(args_model: type[BaseModel]) -> dict:
    raw = args_model.model_json_schema()
    # proxies=False returns plain dicts (json.dumps-able);
    # merge_props would over-merge sibling keys not present in Pydantic output.
    flat = jsonref.replace_refs(raw, proxies=False, lazy_load=False)

    # strip Pydantic / draft-2020-12 metadata
    flat.pop("$schema", None)
    flat.pop("$defs", None)
    flat.pop("$id", None)

    return flat
```

Test surface:

- Round-trip: `Pydantic args_model → input_schema → fake LLM tool_use → parse → Pydantic` produces identical shape.
- No `$ref` in the output; no `$defs` in the output.
- `Optional[T]` fields preserve Pydantic's `anyOf:[T,null]` shape verbatim.
- Existing Anthropic tool-use examples (corpus tools) parse cleanly.

### Spec edits required

- spec:196 — replace "auto-derived from a Pydantic arg model" with "derived
  via `to_anthropic_input_schema(args_model)` in `slop/llm/tools.py`
  (flattens `$ref`, strips draft-2020-12 metadata)"
- spec:280–282 — add `to_anthropic_input_schema` to `slop/llm/tools.py`
  contents
- Task #1 (Gate 1) — explicit deliverable: schema-conversion helper +
  round-trip test
- `jsonref` added to dependencies

---

## #6 — DNS-rebinding guard cannot bind to SDK pool

**Severity:** should-fix — the spec sentence is unimplementable as written,
but on the loopback-default deployment (the spec's normal case, see
spec:185–186, 254, 331) the rebinding window is mostly cosmetic. Original
review framed this as a blocker; the architectural concern is real, the
runtime exposure is small.

### Problem

spec:597 (review's "spec:558"/"spec:664" — the line numbers are off, the
sentence appears once):

> The DNS lookup is repeated per outbound request (TOCTOU mitigation)
> since the initial resolve can change.

What actually happens:

```
user code                    Laminar SDK                  network
─────────                    ───────────                  ───────
Laminar.init(url)  ──►  ┌──────────────────┐
                        │ httpx.Client(...)│
                        │ OTel exporter    │
                        │   keeps own conn │  ──► resolves once
                        │   pool, own DNS  │  ──► caches IP
                        └──────────────────┘  ──► reuses keep-alive
                                │
                                ▼
                        you don't get a hook here
                        ────────────────────────
```

The Laminar SDK manages its own httpx client and OTel exporter. Calling
`socket.gethostbyname()` once at `tracing.init()` does not bind the
result to the SDK's connection pool, and the SDK's later requests will
re-resolve (or use cached connections) without consulting our guard.

### Recommendation

**Path A: fail closed by hard-pinning the resolved IP into the URL.**

```python
def init_tracing(base_url: str, allow_remote: bool = False) -> None:
    parsed = urlparse(base_url)
    host = parsed.hostname
    resolved = socket.gethostbyname(host)
    ip = ipaddress.ip_address(resolved)

    if not (ip.is_loopback or host in PRIVATE_HOST_ALLOWLIST):
        if not allow_remote:
            raise SecurityError(f"refusing tracing to non-loopback {host}")

    # rewrite URL to use the resolved IP, bypassing further DNS
    pinned = parsed._replace(netloc=f"{resolved}:{parsed.port or 443}")
    Laminar.init(base_url=urlunparse(pinned), ...)
```

After this, the SDK never resolves again — there is no second resolution
to TOCTOU. Mention in span attributes that the IP is pinned.

**Caveat for the `LMNR_ALLOW_REMOTE=1` path:** an IP-form URL fails standard
TLS hostname verification because the cert SAN is issued for the hostname,
not the IP. For loopback (the default), the spec uses plain HTTP, so this
doesn't bite. For remote, pair the IP-pinned URL with an explicit
`server_hostname=` SNI override on the underlying transport, or document
that remote deployments accept the (small) rebinding window.

**An earlier Path B (inject a custom httpx transport via `http_client=`) was
considered and dropped:** the Laminar Python SDK's `Laminar.initialize()`
signature does not accept an `http_client` / `transport` parameter (verified
against `lmnr-ai/lmnr-python` `src/lmnr/sdk/laminar.py`). Implementing it
would require either upstreaming the parameter or replacing the OTLP
exporter through OTel internals. Path A is the pragmatic choice.

### Spec edits required

- spec:597 — replace "DNS lookup repeated per outbound request" with "host
  resolved once at init; resolved IP is pinned into `LMNR_BASE_URL` so
  subsequent requests bypass DNS entirely. For `LMNR_ALLOW_REMOTE=1`,
  document the SNI implication."
- Task #1 (Gate 1) — `tracing.py` deliverable: IP-pinning at init,
  explicit test that `Laminar.init` receives an IP-form URL

---

## #7 — Async/sync boundary contradicts itself

**Severity:** should-fix — implementation gap, not architectural error.

### Problem

The spec contradicts itself on whether per-query LLM calls are sync or async:

- spec:202: "Per-query LLM calls remain synchronous (latency-sensitive)"
- spec:529: "All calls are async HTTP via the SDK; … Ctrl-C cancels the
  asyncio task group"

`LLMClient.complete` is referenced without an `async def` / `def`
qualifier, and `synthesize_all` uses `asyncio.gather` (implying async at
that stage), while upstream stages have no specified composition mode:

```
  facet_extract       ─── sync (Anthropic SDK sync? AsyncAnthropic?)
  embed_dense         ─── sync (openai SDK)
  embed_sparse        ─── sync (fastembed)
  qdrant.query_points ─── sync (qdrant-client sync)
  llm_rerank          ─── async? (Sonnet via SDK)
  synthesize_all      ─── async (asyncio.gather over AsyncAnthropic)
  render              ─── sync
```

Two viable shapes:

```
Shape A: fully async                Shape B: sync until synth
────────────────────────             ─────────────────────────
async def run():                    def run():
  await facets()                      facets = facet_extract()
  await embed()                       vecs   = embed()
  await asyncio.to_thread(            cands  = retrieve()
    cross_encoder, ...)               reranked = llm_rerank()
  await synthesize_all()              return asyncio.run(
                                        synthesize_all(reranked))
CPU-bound stages need
to_thread to not block              Single asyncio.run() at the
the event loop                      boundary; simpler
```

### Recommendation

Pick **Shape A** (fully async). Rationale:

- `LLMClient` is async; making one stage sync forces a synchronous wrapper
  (`asyncio.run` per call) that loses the connection pool between stages.
- `qdrant-client` ships an async variant (`AsyncQdrantClient`) — use it.
- `openai` ships `AsyncOpenAI` — use it.
- `fastembed` is sync and CPU-bound; wrap in `asyncio.to_thread()`. Cheap.
- One `asyncio.run()` at the CLI entry point, full async below.

### Spec edits required

- New short subsection under "Architecture > Architectural decisions":
  "Pipeline is fully async; CPU-bound stages dispatch via `asyncio.to_thread`."
- spec:264 (`pipeline.py` docstring) — note the async contract.
- spec:266–272 (stage modules) — note that each stage is `async def`.
- Task #10 (CLI + pipeline orchestration) — single `asyncio.run` entry.

---

## #9 — Anthropic Batches + prompt caching is best-effort

**Severity:** should-fix — cost model assumes a property Anthropic
explicitly documents as best-effort. Re-verified 2026-04-28: Anthropic's
batch-processing docs state cache hits across batched items "are provided
on a best-effort basis. Users typically experience cache hit rates ranging
from 30% to 98%". The spec's implicit "1 write + N-1 reads" is the
optimistic end of that range; planning around the pessimistic end matters.

### Problem

spec:198:

> Ingest fan-out uses the Message Batches API: 500 facet_extract + 500
> summarize calls are submitted as a single batch (50% discount, async)

spec:609:

> Batch discount (50% via Anthropic Message Batches API) applies to the
> bulk ingest path. The previous figure (~$10.30) reflected synchronous
> calls without batching; SDK + Batches roughly halves it.

The cost math implicitly assumes:

```
500 batched calls × shared system block ~3K tokens
       │
       ▼
  ┌────────────────────────────────────────────┐
  │ first batch item writes the cache          │ ← assumed
  │ items 2..500 hit the cache at $0.30/M      │ ← assumed
  │ effective input cost ≈ flat with N         │ ← assumed
  └────────────────────────────────────────────┘
```

If cache writes within a batch are independent (each item creates its
own cache entry), the math is:

```
500 × cache_creation instead of 1× write + 499× read
→ for Haiku 4.5 at $1.00/M input × 1.25× 5m-write multiplier:
  500 × 3K tokens × $1.25/M = $1.875 in cache writes
  vs assumed: 1 × 3K × $1.25/M + 499 × 3K × $0.10/M = $0.154
→ overshoot ~$1.72 on the ingest budget, ~17% of the $10 cap
```

Anthropic's own guidance for batches:
- Identical `cache_control` blocks in every Message in the batch.
- Maintain a steady stream so 5-minute cache entries don't expire mid-batch.
- "Since batches can take longer than 5 minutes to process, consider using
  the 1-hour cache duration with prompt caching for better cache hit rates."

### Recommendation

1. **Use the 1-hour cache TTL** (`cache_control: {type:"ephemeral", ttl:"1h"}`)
   for the batch system block. Anthropic explicitly recommends this for
   batches that may take >5 min. 1h-write multiplier is 2× base input
   instead of 1.25×, but a single write amortized over 500 items is still
   net-cheaper than re-creating the 5m cache mid-batch.

2. **Warm the cache before the batch** by firing one synchronous call with
   the same system block immediately before submission. The batch then
   reads from the existing cache. Cost: one extra Haiku call (~$0.005),
   negligible.

3. **Empirical check** via `usage.cache_read_input_tokens` and
   `usage.cache_creation_input_tokens` on the first 5 batch responses.
   Used to size the budget once, not as a runtime guard.

### Spec edits

- spec:198 — note Batch+cache is best-effort per Anthropic docs; spec
  combines 1h TTL + pre-batch warm call to maximise hit rate
- spec:609 (now spec:646) — fix Haiku 4.5 base input from $0.80/M to
  $1.00/M; recompute the worked example. `max_cost_usd_per_ingest`
  already has 100% headroom so the cap stays at $10.
- Open questions section — add an entry: "Confirm 1h TTL + pre-batch warm
  produces ≥80% cache hit rate empirically before reducing ingest budget."

---

## #10 — Mixed cassette stack (vcrpy + respx)

**Severity:** should-fix — flakiness vector if both are active on the same
client. Both libraries hook httpx at the **transport** layer (respx via
`MockTransport`, vcrpy via `httpx_stubs` patching the transport's request
handler), so the original review's "above SDK vs at transport" framing is
inaccurate. The real risk is whichever transport patch wins shadows the
other within a single test, producing cassette/replay mismatches.

### Problem

spec:729 (review's "spec:694"):

> Tooling: `pytest`, `pytest-asyncio`, `pytest-recording`, `syrupy`,
> `respx` for any non-`requests` HTTP mocking.

```
pipeline calls llm.complete()
          │
          ▼
┌────────────────────────┐
│ AnthropicSDKClient     │
│   .messages.create()   │
└─────────┬──────────────┘
          │
          ▼
┌────────────────────────┐
│ httpx.AsyncClient      │ ◄── respx AND pytest-recording both
│   .post(/v1/messages)  │     hook here (transport layer).
└─────────┬──────────────┘     Whichever patch is active wins.
          │
          ▼
     network


Concrete flake mode:
  • a test has BOTH respx fixture and pytest-recording marker active
  • respx returns "X" instantly
  • SDK retry logic doesn't fire (no transient errors to record)
  • vcrpy cassette has 1 request, but live (record-mode) run had 3 retries
  • next replay → cassette plays 1, SDK expects 3 → mismatch
```

### Recommendation

Two options, both acceptable:

| Choice | When |
|---|---|
| **vcrpy only** | Default. Spec already invests heavily in retry/backoff/caching that should be exercised. |
| **Strict non-overlap rule** | Keep respx for fast unit dispatch tests; restrict to `tests/unit/`. pytest-recording lives only in `tests/integration/`. Fixtures never co-exist in one file. |

For this codebase: prefer **vcrpy/pytest-recording only**, falling back to
the non-overlap rule if respx's ergonomics for unit-level dispatch tests
prove valuable.

### Spec edits required

- spec:729 — either drop `respx`, or document the non-overlap rule
- spec test-strategy section — confirm cassette tooling is `pytest-recording`
  for any test that exercises SDK retry/backoff
- Task #2 (LLMClient + FakeLLMClient cassette) — explicit: cassettes are
  vcrpy / pytest-recording

---

## Nits

### n1 — M-series CPU latency in spec table

spec:215:

> budget ~1–2s wall-clock for 30 candidates on CPU

Resolved as part of issue #2 (cross-encoder dropped). No further action.

### n2 — OpenAI embedding price pin

spec:609 quotes `text-embedding-3-small` at $0.02/M tokens. Correct as of
2026-04. Pin the assumed price into `slop/llm/prices.yml` (or wherever
per-model price tables live) so a price change is a one-line edit, not a
spec re-derivation.

### n3 — SQLite driver named for journal

spec:242 references `data/journal.sqlite`. Stdlib `sqlite3` is the
obvious choice. Commit to it explicitly so concurrent-access semantics
(WAL mode, busy_timeout) are clear:

> `MergeJournal` uses stdlib `sqlite3` in WAL mode with
> `busy_timeout=5000ms`. No connection pool — a single short-lived
> connection per merge action.

Add to Task #3 (Corpus / MergeJournal) deliverable.

---

## Recommended fix order

```
blockers — fix before any implementation:
  #3 markdown path (architectural ambiguity)
  #4 semaphore shrinking (use anyio.CapacityLimiter)

should-fix — fix during implementation, in-task:
  #5 schema-conversion helper (Task #1 — defensive flatten only)
  #6 DNS guard (Task #1 — IP-pinning, drop "repeated DNS" sentence)
  #7 async/sync contradiction (resolve spec:202 vs spec:529, Task #10)
  #9 batch cache hit rate (Task #2 — 1h TTL + pre-batch warm call)
  #10 cassette stack (Task #2 — pick vcrpy or enforce non-overlap)

nits — fix when convenient:
  n2 price pin (also bump Haiku 4.5 to $1.00/M in spec:646)
  n3 sqlite driver (stdlib sqlite3, WAL, busy_timeout=5000ms)
```
