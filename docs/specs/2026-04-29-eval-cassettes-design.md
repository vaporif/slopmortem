# Eval cassettes — design

**Date:** 2026-04-29
**Status:** approved (pending plan review)
**Topic:** Replace hand-written canned LLM responses in the eval runner with a record/replay cassette layer; extend the same machinery to one e2e test and expose it as reusable infrastructure for custom tests.

## Goal

The eval runner today uses hand-written canned responses (`_facet_extract_payload()`, `_synthesis_payload()` etc. in `slopmortem/evals/runner.py`) to drive `FakeLLMClient`. These responses are fictions of what the model might emit — they validate plumbing, not realism. Task 11 of the original implementation plan called for cassette-based replay; the cassette layer was deferred and a `--record` stub was left in place.

This spec closes that gap. After implementation:

- `just eval` runs offline against committed cassette files containing actual past OpenRouter LLM responses and locally-recorded fastembed dense + sparse vectors
- `just eval-record` regenerates cassettes against the live OpenRouter API + the local fastembed models (~$0.50–$1 per full record; LLM-side dollars only — embeddings are free under the default `embedding_provider="fastembed"`)
- `just eval-record-corpus` regenerates the seed corpus fixture (~$0.30–$1 under fastembed default — LLM-side ingest stages only; ~$3–5 if `embedding_provider="openai"` is configured. Run rarely.)
- The eval runs against a real Qdrant collection seeded from a JSONL fixture, exercising the actual retrieval path
- The recording machinery is reusable: `record_cassettes_for_inputs()` is a public Python helper that any test can call to populate its own cassette directory under `tests/fixtures/cassettes/custom/`

**Embedding-provider note.** As of [`local embeddings (#14)`](../../slopmortem/llm/fastembed_client.py), the default `embedding_provider="fastembed"` runs the dense embedder locally via `FastEmbedEmbeddingClient` (model `nomic-ai/nomic-embed-text-v1.5`, 768 dim, ~550 MB ONNX). The OpenAI provider (`text-embedding-3-small`, 1536 dim) is still supported via `embedding_provider="openai"`. Cassettes key on `(model, text_hash)`, so a cassette set is bound to one provider+model — switching providers (or `embed_model_id`) between record and replay produces a loud `NoCannedEmbeddingError` per row, not a silent vector mismatch. See Risk 5 for why we keep recording embedding cassettes even though the default provider is local and free.

## Execution Strategy

**Parallel subagents.** Seven implementable tasks (commit 5 is operator-only, generates the cassettes and corpus fixture against live APIs). All Python with one justfile edit. Each task's CREATE/MODIFY file list is disjoint from the others — file ownership is clean. Per-task review is sufficient; a final cross-stream review covers integration. The persistent-team coordination overhead of `/team-feature` would not pay off here.

**Sequential dispatch.** Per the user's standing preference (one task at a time, parent agent owns commit authorship), each subagent runs to completion and is reviewed before the next dispatches. Subagents must not run `git add` or `git commit`.

## Agent Assignments

| Task | Agent | Domain |
|------|-------|--------|
| 1. Cassette infrastructure (`recording.py`, `cassettes.py`, `fake.py` key widening) | python-development:python-pro | Python |
| 2. Corpus fixture machinery (`corpus_fixture.py` + Qdrant round-trip tests) | python-development:python-pro | Python |
| 3. Recording helper + ephemeral Qdrant context manager | python-development:python-pro | Python |
| 4. Justfile entry points + runner argparse (no behavior change) | python-development:python-pro | Python + justfile |
| 5. **OPERATOR — manual.** Run `just eval-record-corpus` then `just eval-record` against live APIs; commit the generated fixtures, cassettes, and updated baseline. | (no agent) | Real-API spend |
| 6. Switch runner default to cassettes; remove canned helpers | python-development:python-pro | Python |
| 7. Migrate `test_full_pipeline_with_fake_clients` to cassettes | python-development:python-pro | Python |
| 8. Documentation pass (cassette author guide) | python-development:python-pro | Markdown |

## Architecture overview

Seven structural pieces:

1. **Cassette key.** `(template_sha, model, prompt_hash)` for LLM cassettes; `(model, text_hash)` for embedding cassettes (dense and sparse share the schema; sparse uses `model="Qdrant/bm25"`). `prompt_hash = sha256((system or "") + "\x1f" + prompt).hexdigest()[:16]`. The `\x1f` (ASCII unit separator) avoids any chance of the system/prompt boundary aliasing in real prompts.

   **`template_sha` is a structural hash, not just template text.** It folds together: (a) the Jinja template source, (b) the JSON-serialized `tools` list passed to `OpenRouterClient.complete()` (deterministically ordered; empty string when the call passes no tools), and (c) the JSON-serialized `response_format` Pydantic schema (`Synthesis.model_json_schema()` etc.; empty string when absent). Editing a tool description, the `Synthesis` schema, or the tool list invalidates every cassette under that template — exactly what we want. `corpus/taxonomy.yml` flows into the rendered prompt as a Jinja global, so taxonomy edits already invalidate `prompt_hash` and need no extra dimension. `template_sha` is captured verbatim in the cassette JSON and `request_debug`.

2. **`FakeLLMClient` widening.** The existing `canned` map keys on `(template_sha, model)`. The new key is `(template_sha, model, prompt_hash)`. **No wildcard fallback.** Lookup is strict 3-tuple → `NoCannedResponseError` on miss. A wildcard fallback would silently fail-open (a real cassette miss would resolve to the wildcard entry from a colocated test), violating the §"Cassette miss is loud" invariant. Commit 1 widens every existing 2-tuple call site in the same change — `tests/test_pipeline_e2e.py`, `tests/test_observe_redaction.py`, `tests/test_ingest_idempotency.py`, `tests/test_ingest_dry_run.py`, `tests/test_ingest_orchestration.py`, `tests/stages/test_synthesize.py`, `tests/stages/test_llm_rerank.py`, `tests/stages/test_facet_extract.py` — so the type stays consistent across the tree.

3. **`RecordingLLMClient` / `RecordingEmbeddingClient` / `RecordingSparseEncoder`.** Wrap a real client (or, for sparse, the live fastembed BM25 encoder). Each `complete()`, `embed()`, or `encode()` call forwards to the inner; on success, writes one cassette JSON file *per text* (LLM and sparse are 1:1 calls; `embed()` accepts a list and is split). On inner error: do not write, propagate.

   **Cost ceiling (`RecordingLLMClient` only).** Constructor takes `max_cost_usd: float | None = None`. When non-None, the wrapper accumulates each call's `response.cost_usd` and raises `RecordingBudgetExceededError(spent=..., limit=...)` *before* invoking the inner if the next call would push past the limit. On overrun the wrapper does not write the in-flight cassette; tmp_dir cleanup is the caller's responsibility (already handled per the recording flow). Default ceiling at the `just eval-record` call site is **`$2.00`** (≈2× the current full-record estimate of $0.50–$1) — runaway tool loops or model-upgrade surprises hit this before they hit OpenRouter's rate limits. Operator overrides via `--max-cost-usd <float>` if a re-record legitimately needs more. Sparse and embedding wrappers don't carry a ceiling: under the default `embedding_provider="fastembed"` both run locally with `cost_usd=0.0`; under `embedding_provider="openai"` the dense side has a real per-token cost but it's still dominated by the LLM tool-loop side. Either way the LLM ceiling is the meaningful guardrail.

   **Embedding batch split.** Both `OpenAIEmbeddingClient.embed(texts)` and `FastEmbedEmbeddingClient.embed(texts)` are N-in / N-out and return a single `EmbeddingResult(vectors, n_tokens, cost_usd)` aggregate ([slopmortem/llm/openai_embeddings.py:85-120](../../slopmortem/llm/openai_embeddings.py), [slopmortem/llm/fastembed_client.py:83-100](../../slopmortem/llm/fastembed_client.py)). `RecordingEmbeddingClient` is provider-agnostic: it wraps an arbitrary `EmbeddingClient`, iterates `zip(texts, response.vectors)`, and writes one cassette per text keyed `(model, text_hash)`. Cost / token totals are **not** stored on cassettes — neither backend exposes per-input token counts, and today's `FakeEmbeddingClient` already returns `cost_usd=0.0, n_tokens=0`. The eval doesn't assert on embedding cost. Recording's aggregate cost still flows to the budget tracker via the inner real client (zero for fastembed, real $ for OpenAI).

   **Embedding batch reassembly.** On replay, `FakeEmbeddingClient.embed([t1, t2, ...])` performs N independent per-text cassette lookups, returning the vectors in input order with `cost_usd=0.0, n_tokens=0`. The same cassette is reused across batches (a text appearing in two different queries hits one file), which is the main reason for per-text granularity over a batch-shaped cassette.

   The sparse wrapper writes embedding-shaped cassette JSON keyed `(model="Qdrant/bm25", text_hash)` with a sparse `response` payload (see "Sparse-embedding cassette JSON" below). Replay constructs a `CassetteSparseEncoder` from the same dir; production code path through `retrieve.py` is unchanged when no encoder is injected.

4. **Cassette directory layout.** Per-scope, per-call. Each scope (one eval row, one e2e test, one custom-test setup) owns a directory; LLM and embedding cassettes coexist there with prefixed filenames:

```
tests/fixtures/cassettes/
  evals/
    <row_id>/
      embed__nomic-ai_nomic-embed-text-v1.5__<text_hash>.json
      embed__Qdrant_bm25__<text_hash>.json
      facet_extract__anthropic__claude-sonnet-4.6__<prompt_hash>.json
      llm_rerank__anthropic__claude-sonnet-4.6__<prompt_hash>.json
      synthesize__anthropic__claude-sonnet-4.6__<prompt_hash>.json
      ... (one file per fan-out branch)
  e2e/
    test_full_pipeline_with_fake_clients/
      ...
  custom/
    .gitkeep
    <test_or_scenario_name>/
      ...
```

Dense-embedding filenames reflect whichever model was active at record time: `nomic-ai_nomic-embed-text-v1.5` under the default `fastembed` provider, `text-embedding-3-small` (or `-large`) under `openai`. The slugifier (see "Model slug in filenames" below) handles all of `/`, `:`, `@` uniformly, so `nomic-ai/nomic-embed-text-v1.5` → `nomic-ai_nomic-embed-text-v1.5` falls out of the same rule that produces `Qdrant_bm25` and `anthropic_claude-sonnet-4.6_beta`.

Filenames use the full 16-char hash (no truncation) so collision risk is negligible. The repeated `embed__<model>__<hash>.json` per scope (same text re-embedded across scopes) is intentional duplication — keeping every cassette inside its scope is what makes the atomic-swap-per-scope guarantee work.

**Model slug in filenames.** OpenRouter model ids contain `/` (e.g. `anthropic/claude-sonnet-4.6`) and may carry suffixes like `:beta`, `:nitro`, `:free` — `:` is forbidden on Windows filesystems, `/` is interpreted as a path separator. Filenames replace any character not in `[A-Za-z0-9._-]` with `_` (single regex covers `/`, `:`, `@`, and any future surprises). The original unescaped model id stays in the JSON `key.model` field; the filename slug is purely a filesystem concern and is never parsed back. A single helper `_slugify_model(model: str) -> str` in `slopmortem/evals/cassettes.py` does the substitution; both recording wrappers and replay loaders import it.

5. **Corpus fixture.** `tests/fixtures/corpus_fixture.jsonl` (~30 documents). Each line:

```json
{
  "canonical_id": "...",
  "dense": [768 floats],     // 768 under fastembed default; 1536 if embedding_provider="openai" with text-embedding-3-small
  "sparse_indices": [...],
  "sparse_values": [...],
  "payload": { full CandidatePayload shape }
}
```

Generated once by `just eval-record-corpus` running real ingest against the seed-input set at `tests/fixtures/corpus_fixture_inputs.yml` (committed). Eval setup spins an ephemeral Qdrant collection (`slopmortem_eval_<pid>_<uuid4>`), bulk-upserts from this file, drops the collection on teardown.

6. **Two recording commands, two cadences.**

   - `just eval-record-corpus` — rare. Real ingest of seed docs → JSONL. Run when seed corpus needs to change. ~$0.30–$1 under the default `embedding_provider="fastembed"` (LLM-side ingest stages — summarize/extract/etc. — only); ~$3–5 if `embedding_provider="openai"` is configured because dense embeddings then go to the paid API. Either way, expect a one-time fastembed model download (nomic ~550 MB + BM25 ~150 MB ≈ ~700 MB) on the first run if `slopmortem embed-prefetch` hasn't been run.
   - `just eval-record [--scope evals/<row_id>]` — frequent. Replay queries with cassetting → cassette files + updated baseline. Run when prompts/models change. ~$0.50–$1 full, ~$0.05–$0.10 per scope. Embedding side is free under the default fastembed provider; the dollar figure is OpenRouter LLM cost only.

7. **Cassette miss is loud.** No silent fallback, no auto-record-on-miss. `FakeLLMClient` raises `NoCannedResponseError` with the missing key and the list of recorded keys (existing behavior). Recording is always explicit.

### Data flow at replay time

```
JSONL fixture → ephemeral Qdrant collection
                      ↓
InputContext → FakeEmbeddingClient (dense cassette) ┐
              CassetteSparseEncoder (sparse cassette) ┘ → Qdrant query → top K candidates
                      ↓
              FakeLLMClient (cassette) — facet, rerank, synthesize × N
                      ↓
              Synthesis × N → assertions → baseline diff → exit code
```

### Data flow at record time

```
InputContext → RecordingEmbeddingClient → real EmbeddingClient (fastembed by default, OpenAI if opted in) → dense cassette file
              RecordingSparseEncoder    → live fastembed BM25 → sparse cassette file
                      ↓
                 Qdrant query (real fixture)
                      ↓
              RecordingLLMClient → real OpenRouter → cassette file
                      ↓
                 results → baseline.json (v2 with corpus_fixture_sha256 + recording_metadata)
```

## Component breakdown

### New files

| Path | Purpose |
|------|---------|
| `slopmortem/evals/recording.py` | `RecordingLLMClient`, `RecordingEmbeddingClient`. Forward to a real client; write cassettes on success. Lives under `evals/` because it imports `evals.cassettes` (key derivation, slug helper) — keeps the layering one-way (`evals → llm`, never `llm → evals`). |
| `slopmortem/evals/cassettes.py` | Loaders, key derivation (`llm_cassette_key`, `embed_cassette_key`), slugifier, error types (`CassetteFormatError`, `CassetteSchemaError`, `DuplicateCassetteError`, `NoCannedEmbeddingError`, `RecordingBudgetExceededError`). |
| `slopmortem/evals/qdrant_setup.py` | `setup_ephemeral_qdrant()` async context manager. Imported by `runner.py` and `recording_helper.py`; no helper-to-runner edge. |
| `slopmortem/evals/corpus_fixture.py` | `dump_collection_to_jsonl`, `restore_jsonl_to_collection`, `compute_fixture_sha256`. |
| `slopmortem/evals/recording_helper.py` | `record_cassettes_for_inputs()` — Layer 2 reusable orchestration. |
| `tests/fixtures/corpus_fixture_inputs.yml` | Hand-curated seed-input list (~30 entries) — committed source of truth. |
| `tests/fixtures/corpus_fixture.jsonl` | Generated artifact (~1.5MB) — committed via Git LFS (regenerable, opaque vector floats). |
| `.gitattributes` | LFS filter line for `tests/fixtures/corpus_fixture.jsonl` only. Cassettes stay in regular git so per-file diffs survive. |
| `tests/fixtures/cassettes/evals/<row_id>/*.json` | Per-row LLM + embedding cassettes. Generated. |
| `tests/fixtures/cassettes/e2e/test_full_pipeline_with_fake_clients/*.json` | Cassettes for the migrated e2e test. Generated. |
| `tests/fixtures/cassettes/custom/.gitkeep` | Reserved subtree for ad-hoc cassette sets. |
| `tests/test_cassettes.py` | Loader + key derivation unit tests. |
| `tests/test_recording.py` | Recording wrapper unit tests with `FakeLLMClient` as inner; covers LLM, dense embedding, and sparse encoder wrappers. |
| `tests/evals/test_corpus_fixture.py` | JSONL dump/restore round-trip tests (`requires_qdrant`). |
| `tests/evals/test_recording_helper.py` | Atomic-swap, tmp_dir cleanup, scope-dir lifecycle. |
| `tests/evals/test_runner_replay.py` | Runner-level integration tests (`requires_qdrant`). |
| `tests/evals/test_fixtures/tiny_corpus.jsonl` | Small fixture for runner-replay tests (~3 docs). |
| `tests/evals/test_fixtures/cassettes/...` | Hand-built cassettes for runner-replay tests. |

### Modified files

| Path | Change |
|------|--------|
| `slopmortem/llm/fake.py` | Widen `canned` key to `(template_sha, model, prompt_hash)`. Strict lookup — no wildcard fallback. |
| `slopmortem/llm/fake_embeddings.py` | Add optional `canned: Mapping[(text_hash, model), list[float]]`. When `canned is not None`, lookup is strict and raises `NoCannedEmbeddingError` on miss (no sha-derived fallthrough). When `canned is None`, today's sha-derived behavior is preserved. Symmetric with the LLM rule. The cassette-backed path is provider-agnostic: the canned map's `model` field can be any string in `EMBED_DIMS` (default `nomic-ai/nomic-embed-text-v1.5`, opt-in `text-embedding-3-small` / `-large`). |
| `slopmortem/pipeline.py` | `run_query` accepts an optional `sparse_encoder: SparseTextEncoder \| None` and forwards it to `retrieve()`. Default `None` keeps the existing fastembed lazy-load. Eval/recording paths inject a `RecordingSparseEncoder` (record) or a cassette-backed encoder (replay) so sparse vectors don't require live fastembed at replay time. |
| `slopmortem/stages/retrieve.py` | Trivial — already accepts `sparse_encoder` parameter; this row is kept for visibility because the production lazy-load is no longer the only path. |
| `slopmortem/evals/runner.py` | Replace `_build_canned()` and canned helpers with cassette loading. Replace `_EvalCorpus` with `setup_ephemeral_qdrant` context. Add `--scope` arg (applies to both record and replay). Wire `--record` to actually record. Add corpus fixture SHA mismatch warning. Bump `_BASELINE_VERSION = 2`; teach `_serialize_results`, `_diff_against_baseline`, and `--write-baseline` to round-trip `corpus_fixture_sha256` + `recording_metadata` without clobbering. Import `NoCannedResponseError`; pre-check cassette dir existence; wrap `run_query` per row in `try/except NoCannedResponseError` and record a sentinel result + emit `FAIL <row_id>: …`. |
| `tests/test_pipeline_e2e.py` | Migrate `test_full_pipeline_with_fake_clients` to cassettes + ephemeral Qdrant. Mark `requires_qdrant`. Other two tests unchanged. |
| `tests/evals/baseline.json` | Add `version: 2`, `corpus_fixture_sha256`, `recording_metadata` fields. Existing `rows` shape unchanged. |
| `justfile` | Rewire existing `eval-record` recipe (already in `justfile:24-25`) to drive the new recording flow. Add new `eval-record-corpus` target. |
| `flake.nix` | Add `git-lfs` to the dev shell `packages` list (currently `flake.nix:100-121` does not include it). Without this, contributors who follow the existing onboarding flow get an LFS pointer file on clone instead of the corpus fixture. |

### Files NOT touched

- `slopmortem/stages/*.py` — cassette mechanism is invisible to stages (other than `retrieve.py` which already accepts the sparse encoder, see Modified files)
- `slopmortem/cli.py` — `_build_deps` reused for `--live` mode
- `slopmortem/corpus/qdrant_store.py` — existing `QdrantCorpus` used as-is
- `tests/stages/*`, `tests/corpus/*`, `tests/smoke/*` — keep canned `FakeLLMClient` setups (testing plumbing, not realism)
- `slopmortem/evals/assertions.py` — pure, signature-stable

### Module dependency direction (no cycles)

```
slopmortem/evals/
  runner.py ──→ cassettes.py ──→ slopmortem/llm/fake.py, fake_embeddings.py
     │              ▲
     │              └── recording.py ──→ slopmortem/llm/openrouter.py
     │                                    slopmortem/llm/fastembed_client.py    # default
     │                                    slopmortem/llm/openai_embeddings.py   # opt-in
     │                                    slopmortem/corpus/embed_sparse.py (BM25 encoder)
     │
     ├──→ recording_helper.py ──→ recording.py + qdrant_setup.py
     │
     ├──→ qdrant_setup.py ──→ slopmortem/corpus/qdrant_store.py
     │
     └──→ corpus_fixture.py ──→ slopmortem/corpus/qdrant_store.py
```

Imports are one-way `evals → llm` and `evals → corpus`. Recording wrappers live in `slopmortem/evals/recording.py` (not `slopmortem/llm/`) so the production LLM surface stays free of test infrastructure and `llm` never imports from `evals`.

## Data shapes

### LLM cassette JSON

```json
{
  "schema_version": 1,
  "key": {
    "template_sha": "<full hex>",
    "model": "anthropic/claude-sonnet-4.6",
    "prompt_hash": "<16 hex chars>"
  },
  "response": {
    "text": "<final assistant text>",
    "stop_reason": "stop",
    "cost_usd": 0.0234,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 1840
  },
  "request_debug": {
    "prompt_preview": "first 500 chars",
    "system_preview": "first 500 chars",
    "tools_present": ["get_post_mortem", "search_corpus"],
    "response_format_present": true,
    "recorded_at": "2026-04-29T13:45:00Z",
    "recording_run_id": "eval-record-2026-04-29T13:45-<uuid>"
  }
}
```

`key` and `response` are normative. `request_debug` is human-only diagnostic; not load-bearing. `tools_present` lists tool names but not call traces — tool loops are internal to `OpenRouterClient.complete()`, only the final response is captured (see "Risks and invariants" below for the implication).

### Embedding cassette JSON

```json
{
  "schema_version": 1,
  "key": {
    "model": "nomic-ai/nomic-embed-text-v1.5",
    "text_hash": "<16 hex chars>"
  },
  "response": {
    "vector": [0.0123, -0.0456, ...]
  },
  "request_debug": {
    "text_preview": "first 200 chars",
    "vector_dim": 768,
    "recorded_at": "..."
  }
}
```

The `key.model` field is whatever string `EmbeddingClient.model` returned at record time. The default fastembed provider produces `"nomic-ai/nomic-embed-text-v1.5"` (768 dim); `"text-embedding-3-small"` (1536 dim) and `"text-embedding-3-large"` (3072 dim) are the OpenAI-provider variants. `vector_dim` is informational — the loader trusts the model registry (`EMBED_DIMS`) and asserts on dim mismatch.

### Sparse-embedding cassette JSON

```json
{
  "schema_version": 1,
  "key": {
    "model": "Qdrant/bm25",
    "text_hash": "<16 hex chars>"
  },
  "response": {
    "indices": [12, 47, 392, ...],
    "values": [0.341, 0.118, 0.067, ...]
  },
  "request_debug": {
    "text_preview": "first 200 chars",
    "nnz": 14,
    "recorded_at": "..."
  }
}
```

Same envelope as the dense embedding cassette (`schema_version`, `key`, `response`, `request_debug`); discriminator is the `key.model` value (`"Qdrant/bm25"` for sparse, `"nomic-ai/nomic-embed-text-v1.5"` / `"text-embedding-3-small"` etc. for dense). Loaders dispatch by model, so a single `load_embedding_cassettes()` call returns one map of dense vectors and one of sparse `(indices, values)` tuples — both keyed `(model, text_hash)`.

### Corpus fixture JSONL

One JSON object per line, schema described under Architecture overview. ~50KB per document, ~1.5MB total for 30 docs. Committed.

### Augmented `baseline.json`

```json
{
  "version": 2,
  "corpus_fixture_sha256": "<sha256 hex of corpus_fixture.jsonl>",
  "recording_metadata": {
    "recorded_at": "...",
    "models": {
      "facet": "anthropic/claude-haiku-4.5",
      "rerank": "anthropic/claude-sonnet-4.6",
      "synthesize": "anthropic/claude-sonnet-4.6",
      "embedding": "nomic-ai/nomic-embed-text-v1.5",
      "embedding_provider": "fastembed"
    }
  },
  "rows": { existing v1 row shape }
}
```

`embedding_provider` is captured alongside `embedding` so a baseline reader can tell whether the cassette set was recorded under the local fastembed default or against OpenAI. A provider/model mismatch between baseline and live config doesn't fail the eval on its own (cassette miss surfaces it loudly per-row), but it's recorded so reviewers see the intended setup at a glance.

Loader supports both versions. Missing `corpus_fixture_sha256` (v1) skips the SHA mismatch warning silently — forward-compat. Missing `embedding_provider` in a v2 baseline is also tolerated (forward-compat with cassette sets recorded before this field was added).

### Cassette key derivation

```python
def template_sha(template_text: str, tools: list[dict] | None, response_format: type[BaseModel] | None) -> str:
    """Structural hash of a template: source + tools list + response_format schema.
    Any of the three changing invalidates every cassette under the template."""
    parts = [
        template_text,
        json.dumps(tools or [], sort_keys=True, separators=(",", ":")),
        json.dumps(response_format.model_json_schema() if response_format else {},
                   sort_keys=True, separators=(",", ":")),
    ]
    return sha256("\x1e".join(parts).encode("utf-8")).hexdigest()  # full hex, not truncated

def llm_cassette_key(prompt: str, system: str | None, template_sha: str, model: str) -> tuple[str, str, str]:
    h = sha256(((system or "") + "\x1f" + prompt).encode("utf-8")).hexdigest()[:16]
    return (template_sha, model, h)

def embed_cassette_key(text: str, model: str) -> tuple[str, str]:
    h = sha256(text.encode("utf-8")).hexdigest()[:16]
    return (model, h)
```

`embed_cassette_key` is shared by dense and sparse embedding cassettes; the discriminator is `model` (`"nomic-ai/nomic-embed-text-v1.5"` for dense under the fastembed default, `"text-embedding-3-small"` / `-large` under the OpenAI opt-in, `"Qdrant/bm25"` for sparse). All three functions live in `slopmortem/evals/cassettes.py` and are imported by both recording wrappers and replay loaders. Single source of truth.

## Recording flow

### `just eval-record-corpus` (rare)

**One-time precondition.** Run `slopmortem embed-prefetch` once per machine to warm the fastembed cache (~700 MB across the nomic dense + BM25 sparse models). Without this, the first `eval-record-corpus` invocation pauses on a cold-start HuggingFace download mid-run. The dev shell does not auto-prefetch.

```
1. Read tests/fixtures/corpus_fixture_inputs.yml
2. Spin ephemeral Qdrant collection slopmortem_corpus_record_<pid>_<uuid4>
3. Run real slopmortem.ingest.run_ingest() against those inputs
   (uses the configured embedding_provider — fastembed by default; opt in to
   openai with embedding_provider="openai" + OPENAI_API_KEY if you want the
   corpus fixture to carry OpenAI vectors)
4. dump_collection_to_jsonl(client, collection, "corpus_fixture.jsonl.recording")
5. os.replace("corpus_fixture.jsonl.recording", "corpus_fixture.jsonl") (atomic)
6. Drop ephemeral collection (try/finally)
7. Print SHA256 + point count + active embedding_provider + model + suggest "now run just eval-record"
```

### `just eval-record [--scope <consumer>/<id>]` (frequent)

```
1. Compute corpus_fixture_sha256 from current corpus_fixture.jsonl
   (fail loudly if file missing — point to eval-record-corpus)

2. Spin ephemeral Qdrant collection slopmortem_eval_<pid>_<uuid4>
   restore_jsonl_to_collection from corpus_fixture.jsonl

3. Sweep stale tmp dirs:
   for d in <cassettes_root>.glob("**/*.recording"):
       if d.stat().st_mtime < now - 24h: shutil.rmtree(d, ignore_errors=True)
   (only orphaned `.recording` siblings are touched; live runs use unique names — see step 4a)

4. For each scope (filtered by --scope or all):
   a. tmp_dir = <scope_dir>.{pid}.{uuid4().hex}.recording/  (mkdir; uniqueness avoids cross-process collisions, so no "already exists" refusal needed)
   b. Build real LLMClient + EmbeddingClient + sparse encoder (via _build_deps)
   c. Wrap in RecordingLLMClient + RecordingEmbeddingClient + RecordingSparseEncoder (out_dir=tmp_dir)
   d. Run pipeline.run_query(..., sparse_encoder=recording_sparse) for that input
      → cassettes accumulate in tmp_dir
   e. On success — two-step rename (POSIX rename(2) requires empty dest):
        if real_dir.exists(): os.replace(real_dir, real_dir + ".old")
        os.replace(tmp_dir, real_dir)
        shutil.rmtree(real_dir + ".old", ignore_errors=True)  # best-effort
      A SIGKILL between the two replaces leaves either the old dir intact under
      ".old" or the new dir under real_dir; no window in which real_dir is a
      half-populated tmp_dir under the canonical name. Leftover ".old" dirs are
      idempotently cleaned next run.
   f. On failure: shutil.rmtree(tmp_dir); re-raise; real_dir untouched

4. Build new baseline.json with corpus_fixture_sha256 + recording_metadata + rows
   (with --scope: only that row's entry updated; others preserved)
   Atomic write via baseline.json.recording → os.replace

5. Drop ephemeral Qdrant collection
6. Print summary: scopes recorded, total cost, baseline SHA
```

### Failure rules during recording

| Failure | Rule |
|---------|------|
| Inner LLM/embed call raises | No cassette written. Propagate. tmp_dir cleaned up. Real dir untouched. |
| Cassette write fails (disk, permission) | Propagate. tmp_dir cleaned up. |
| Pipeline raises mid-scope | tmp_dir cleaned up. Real dir untouched. Operator re-runs. |
| `RecordingLLMClient` cost ceiling exceeded | `RecordingBudgetExceededError(spent, limit)` raised before next inner call. tmp_dir cleaned up. Real dir untouched. Operator re-runs with `--max-cost-usd <larger>` if intentional, or investigates a runaway tool loop / model upgrade. |
| Process killed mid-scope | tmp_dir orphaned. Cleaned by next run's startup sweep of `*.recording` older than 24 h. The unique pid+uuid suffix means a sibling worker's live tmp_dir is never younger than 24 h, so the sweep is xdist-safe. |
| Process killed between the two `os.replace` calls | `<scope_dir>.old` stays on disk; next run's atomic-swap step idempotently retries `shutil.rmtree(real_dir + ".old")` before its own swap. No half-populated canonical dir window. |
| Ephemeral Qdrant collection leak | `try/finally` drops it. No startup sweep — see Risk 4 for why a prefix-wide sweep is unsafe. Orphans from `kill -9` are identifiable by the embedded pid+uuid in the collection name and dropped manually. |

Atomicity is per-scope, not global: if rows 1–6 succeed and row 7 fails, rows 1–6 swapped in cleanly; row 7's tmp_dir cleaned up; rows 8–10 never started. Operator re-runs `--scope evals/<row7>` to fix.

Recording does NOT compare against baseline — it writes a fresh one.

## Replay flow

### `just eval` (default — cassettes, no live calls)

```
1. Verify corpus_fixture.jsonl exists (fail with pointer if missing)

2. Load baseline.json
   - If version >= 2 and corpus_fixture_sha256 present:
       compute current SHA → if mismatch, print WARN line
       (does NOT exit non-zero — operator decides when to re-record)
   - If version 1: skip SHA check silently

3. Spin ephemeral Qdrant collection, restore from corpus_fixture.jsonl

4. Load dataset → list[InputContext]
   If --scope was passed (e.g. evals/<row_id>), filter the list to rows whose
   scope matches. An unknown --scope (no row matches) is a fatal "unknown
   scope" error with the list of valid scopes. Without --scope, run all rows.

5. For each row:
   - cassette_dir = tests/fixtures/cassettes/evals/<row_id>/
   - if missing or empty: log "FAIL <row_id>: no cassettes", record regression, continue
   - load_llm_cassettes + load_embedding_cassettes from cassette_dir
     (the embedding loader returns a dense map and a sparse map; the dense map
     feeds FakeEmbeddingClient, the sparse map feeds CassetteSparseEncoder)
   - Build FakeLLMClient + FakeEmbeddingClient + CassetteSparseEncoder with the canned maps
   - try: report = await run_query(..., sparse_encoder=cassette_sparse)
   - except NoCannedResponseError as exc: log "FAIL <row_id>: cassette miss",
                                          record regression, continue
   - score_report(report) → assertion results

6. Drop ephemeral Qdrant collection (try/finally)

7. Diff results vs baseline → emit per-row PASS/FAIL, regressions to stderr
   When --scope was passed, diff only the filtered rows. --write-baseline (if
   set) updates only those rows' entries in baseline.json and preserves the
   rest of the v2 metadata + the un-touched rows verbatim.

8. Exit 0 if no regressions; 1 if any regression. SHA mismatch is warning only.
```

### `just eval --live` (operator verification — real APIs, no cassettes)

Existing `--live` path. Use case: "did the model regress against my recorded cassettes?" If `just eval` (cassettes) passes but `just eval --live` (real API) fails, cassettes are stale relative to current model behavior. Operator decides whether to re-record.

### Cassette miss = regression, not crash

Critical behavior: if a row's cassette directory is missing or a specific cassette key isn't found, runner does **not** crash. Logs the failure, marks the row as a regression, continues to the next row. Reasons:

- Crashing on first miss masks all subsequent misses. Operator wants "tell me everything that's wrong" in one run.
- Error message includes the missing key and the recorded keys (existing `NoCannedResponseError` behavior).
- Re-recording the affected scope (`just eval-record --scope evals/<row>`) fixes it without a full re-record.

### Shared `setup_ephemeral_qdrant()` helper

Lives in `slopmortem/evals/qdrant_setup.py` (its own module, not `runner.py`) — `runner.py` and `recording_helper.py` both import it; co-locating it in `runner.py` would force `recording_helper.py → runner.py`, which violates the layering rule.

```python
@asynccontextmanager
async def setup_ephemeral_qdrant(
    fixture_path: Path,
    *,
    qdrant_url: str = "http://localhost:6333",
    collection_prefix: str = "slopmortem_eval_",
) -> AsyncIterator[QdrantCorpus]:
    """Spin a uniquely-named collection, populate from JSONL, drop on exit.

    Collection name: f"{collection_prefix}{os.getpid()}_{uuid4().hex}".
    No startup sweep — uniquely-named collections are dropped via try/finally;
    orphans from process kills require manual cleanup. See "Risk 4" for why a
    prefix-wide sweep is unsafe under pytest-xdist.
    """
```

Used by `runner.main()`, `record_cassettes_for_inputs()`, and any custom test.

## Three-layer reusable surface

The recording machinery is exposed as three layers so test authors can pick the right level for their use case.

### Layer 1 — primitives

```python
from slopmortem.evals.recording import RecordingLLMClient, RecordingEmbeddingClient, RecordingSparseEncoder
from slopmortem.evals.cassettes import load_llm_cassettes, load_embedding_cassettes
```

For tests that want full control: build your own clients, wrap them, run your pipeline, write cassettes wherever you want. **Layer-1 callers are responsible for setting `config.enable_tavily_synthesis=False` themselves** — the recording wrappers don't override config, so a Layer-1 caller that records with Tavily on will silently bake live-web content into cassettes (see Risk 6).

### Layer 2 — orchestration helper

```python
from slopmortem.evals.recording_helper import record_cassettes_for_inputs

await record_cassettes_for_inputs(
    inputs=[InputContext(name="my_scenario", description="...")],
    output_dir=Path("tests/fixtures/cassettes/custom/my_scenario/"),
    corpus_fixture_path=Path("tests/fixtures/cassettes/custom/my_scenario/corpus.jsonl"),
    max_cost_usd=2.0,  # default; raise for runs that legitimately need more
)
```

`corpus_fixture_path` is required (no default). Test authors choose: commit a per-test corpus JSONL fixture (focused; cassettes invalidate only when *that* fixture changes), or pass the canonical eval fixture (`tests/fixtures/corpus_fixture.jsonl`) and accept that custom-test cassettes go stale every time the eval seed regenerates. Coupling custom-test cassettes to "whatever the eval corpus happens to be today" is the same trap that motivated the cassette layer — making the path explicit forces the choice. Layer 2 also forces `enable_tavily_synthesis=False` on the `Config` it builds, so Layer-2 callers don't have to think about Risk 6.

Handles ephemeral Qdrant setup, real client construction, recording wrappers, atomic dir swap. Use for ad-hoc test scenarios, exploratory recording, reproducing bug reports.

### Layer 3 — `just eval-record` (opinionated)

Calls Layer 2 internally but is strict about layout (writes only to `tests/fixtures/cassettes/evals/`, regenerates `baseline.json`, validates `corpus_fixture_sha256`). Single purpose: maintain the canonical eval cassettes.

### Author workflow for a custom test

1. Decide the corpus fixture: either reuse `tests/fixtures/corpus_fixture.jsonl` (cassettes will invalidate when the eval seed regenerates) or `dump_collection_to_jsonl()` a focused per-test fixture into `tests/fixtures/cassettes/custom/<test_name>/corpus.jsonl` (cassettes only invalidate when that file changes). Commit the chosen fixture.
2. Write the test scaffold using `load_llm_cassettes` + `setup_ephemeral_qdrant(corpus_fixture_path=...)` + `FakeLLMClient`. It will fail at first because no cassettes exist yet.
3. Run a one-off recording script (or `python -c`, or a justfile target) that calls `record_cassettes_for_inputs(inputs=..., output_dir=tests/fixtures/cassettes/custom/<test_name>/, corpus_fixture_path=<chosen path>)`.
4. Cassettes appear. Commit them along with the test.
5. From now on, the test runs offline (Docker Qdrant required, no API keys).
6. When prompts later change → cassette miss → `NoCannedResponseError` with the missing key. Re-run the recording script. New cassettes commit.

## Error handling

### Cassette-side

| Condition | Behavior |
|-----------|----------|
| Cassette directory missing for a row | Log `FAIL <row_id>: no cassettes`. Mark regression. Continue. |
| Cassette key not found during `complete()` | `NoCannedResponseError` caught per-row. Log. Mark regression. Continue. |
| Cassette key not found during `embed()` (when `canned is not None`) | `NoCannedEmbeddingError` caught per-row, same handling as `NoCannedResponseError`. Symmetric with the LLM rule. When `canned is None`, today's sha-derived behavior is preserved (no error). |
| Cassette key not found during sparse `encode()` (when `canned is not None`) | `NoCannedEmbeddingError` (same class — the sparse encoder is a thin embedding-shaped wrapper). |
| Cassette JSON malformed | `CassetteFormatError`. **Fatal** — operator must fix before runs. |
| Schema version unknown | `CassetteSchemaError`. Fatal. |
| Two files with the same key in one scope | `DuplicateCassetteError` naming both paths. Fatal. |
| Cassette write fails mid-recording | Propagate. tmp_dir cleaned up. |

### Corpus-side

| Condition | Behavior |
|-----------|----------|
| `corpus_fixture.jsonl` missing | Fatal. Print pointer to `just eval-record-corpus`. Exit 2. |
| `corpus_fixture.jsonl` malformed | Fatal at restore time. Exit 2. |
| Qdrant unreachable | Fatal. Print pointer to `docker compose up -d qdrant`. Exit 2. |
| `corpus_fixture_sha256` mismatch | **Warning only.** Print WARN line. Don't exit. Operator decides. |
| Ephemeral collection name collision | Re-roll UUID once; if collision repeats raise (≈10⁻³⁸). |
| Leaked ephemeral collection from prior crash | No startup sweep (see Risk 4). The collection's embedded pid+uuid identifies it as a leak; operator drops it manually via Qdrant UI or `qdrant_client.delete_collection`. |

### Loud vs warning rule

**Loud (exit ≠ 0):** structural problems (malformed cassette, missing fixture, Qdrant down, schema version unknown), regressions vs baseline, cassette misses (treated as per-row regression).

**Warning (exit 0):** corpus fixture SHA mismatch, row in current that's missing from baseline (forward-compat), candidate in baseline missing from current with no true assertions.

Principle: structural problems crash with a useful message. Drift produces warnings the operator can ignore until ready to re-record.

## Risks and invariants

### Risk 1: tool-call semantics during recording

**The risk.** `OpenRouterClient.complete()` runs an internal tool loop ([slopmortem/llm/openrouter.py:120-187](../../slopmortem/llm/openrouter.py)). When the model emits `finish_reason='tool_calls'`, the client calls the local tool function (`get_post_mortem` / `search_corpus`), appends the result, and re-sends. The wrapping `RecordingLLMClient` sees one `complete()` call → writes one cassette. The cassette captures the *final* response only; intermediate tool round-trips are not recorded.

**Implication.** On replay, `FakeLLMClient` returns the cassette's final response without re-running tool calls. That's fine for the eval's assertions. But it creates a hidden invariant:

> If `corpus_fixture.jsonl` changes, every cassette whose recording invoked a tool call returns text grounded in the *old* corpus state. The eval still passes (text is the same as recorded), but it's silently testing against stale ground truth.

**Mitigation: corpus fixture SHA in baseline.** At record time, runner writes `corpus_fixture_sha256` into `baseline.json`. At replay time, runner computes the live fixture's SHA and warns on mismatch. Operator sees the warning, decides whether to re-record. Detection automatic, not silent.

This is the cheapest correct mitigation. Alternatives considered:
- Disable tools in synthesis during eval: loses fidelity, eval no longer exercises the tool-call path.
- Capture intermediate tool round-trips in cassettes: significant complexity (cassette becomes a list of round-trips, replay simulates the loop). Overkill for eval purposes.

**Cost / cache fields are tool-loop sums.** `OpenRouterClient.complete()` accumulates `cost_usd`, `cache_read_tokens`, and `cache_creation_tokens` across every turn of the tool loop ([slopmortem/llm/openrouter.py:128-133](../../slopmortem/llm/openrouter.py)). The cassette's `response.cost_usd` etc. reflect the *aggregate* over all turns, not a single API call. Replay assertions and any baseline-tracked cost field therefore measure aggregate behavior — fine for "did total cost regress?" but not for "which turn was expensive". Documenting it here so the cassette schema reader doesn't mistake the field for per-turn semantics.

### Risk 2: prompt template non-determinism

**The risk.** Cassettes are useless if the rendered prompt drifts between record and replay. Any Jinja template that embeds `today`, `now`, a random UUID, or non-stable dict iteration produces a fresh `prompt_hash` every run → 100% cassette miss.

**Mitigation.** Audit `slopmortem/llm/prompts/*.j2` during plan-writing. Required: every template renders deterministically given the same `InputContext` + `Config`. The known parameter-driven dates (`cutoff_iso`) are fine because they flow from inputs.

If non-determinism is found in a template, fix it (parameterize the value, freeze it via fixture) before recording. Document the audit result in the plan.

### Risk 3: filename collision on truncated hash

**The risk.** Earlier sketches used `<prompt_hash[:8]>` in filenames. Two different prompts in the same template+model that collide on the first 8 hex chars → same filename → silent overwrite. Probability ~1/4B per pair; negligible at this scale, but trivially fixable.

**Mitigation.** Filenames use the full 16-char hash. No truncation in the filename anywhere. The full hash is also stored inside the JSON's `key.prompt_hash` field — single source of truth.

### Risk 4: ephemeral Qdrant collection leak

**The risk.** If the runner or recorder dies mid-run (SIGINT, SIGKILL, OOM), the per-run collection isn't dropped. Collections accumulate in the local Qdrant indefinitely.

**Mitigation.** Single layer:

1. `setup_ephemeral_qdrant()` is an async context manager with `try/finally` that drops its own collection on exit. Collection names embed `pid + uuid4` (`slopmortem_eval_<pid>_<uuid4>` / `slopmortem_corpus_record_<pid>_<uuid4>`) so a leak from `kill -9` is identifiable and droppable manually.

**No startup sweep.** Two sweep designs were considered and rejected: (a) blanket `slopmortem_eval_*` deletion on entry — under `pytest -n auto` would race sibling workers and silently drop their live collections mid-run; (b) "delete collections whose embedded pid is no longer alive" — pid reuse on long-lived hosts (CI runners, dev workstations) means a recycled pid masquerades as a live owner forever, so leaks accumulate anyway. With the per-pid+uuid name we accept manual cleanup of orphans (rare) in exchange for never racing live siblings. The Qdrant UI makes orphans visible, and the cost per orphan is a few KB of collection metadata.

**Future escalation path.** If manual cleanup ever becomes painful, skip pid-based sweep entirely (it can't beat reuse) and add time-based GC: at helper entry, drop `slopmortem_eval_*` / `slopmortem_corpus_record_*` collections whose creation timestamp is older than 24 h. No liveness signal required; the only invariant is "no run takes more than 24 h," which is trivially true. Until that escalation is justified, manual cleanup wins on simplicity.

### Risk 5: fastembed model load cost (dense + sparse) and replay determinism

**The risk.** Both the dense and sparse encoders are now local fastembed ONNX models under the default config:

- Dense: `nomic-ai/nomic-embed-text-v1.5` (~550 MB) via `FastEmbedEmbeddingClient` — the default per `slopmortem/config.py:43-44`.
- Sparse: `Qdrant/bm25` (~150 MB) via `slopmortem/corpus/embed_sparse.py`.

Without recording, every replay would (a) download ~700 MB total on cold-start CI runners, (b) load both models into memory each test session (~3–5 s combined), and (c) leak a HuggingFace network dependency into a "no API keys, no network" eval claim. fastembed inference is deterministic given the same model + text, but FP rounding can vary across CPUs / SIMD paths / fastembed versions, so replays on a different host can drift from the recorded vectors at the LSB.

**Mitigation.** Treat both encoders as recordable surfaces, even though neither costs money:

- Dense: `RecordingEmbeddingClient` wraps whichever `EmbeddingClient` the configured provider returns (`FastEmbedEmbeddingClient` by default, `OpenAIEmbeddingClient` if opted in) and writes one cassette per text keyed `(model, text_hash)`.
- Sparse: `RecordingSparseEncoder` wraps the live BM25 encoder during `eval-record`, writing one sparse cassette per query text keyed `(model="Qdrant/bm25", text_hash)`.

`pipeline.run_query` accepts an optional `sparse_encoder` parameter — replay injects a `CassetteSparseEncoder` backed by the loaded cassettes; record injects the recording wrapper; production paths pass nothing and keep the existing fastembed lazy-load. Stored-document sparse vectors come precomputed in the JSONL fixture (same as today). Net effect: replay does zero fastembed work, zero ONNX runtime startup, zero HuggingFace I/O, and is hardware-FP-independent. CI cost is only cassette load (microseconds per row).

The cassette key already includes `model`, so flipping `embedding_provider` between fastembed and openai (or changing `embed_model_id`) automatically invalidates the dense cassettes (loud `NoCannedEmbeddingError`, not silent vector mismatch).

### Risk 6: Tavily live-web tools are unfixtured

**The risk.** When `config.enable_tavily_synthesis=True`, `slopmortem/llm/tools.py:80-131` registers `tavily_search` / `tavily_extract` and the OpenRouter tool loop runs them inline during synthesis. `RecordingLLMClient` only sees the final assistant text, so the cassette captures whatever the live web returned the day we recorded. `corpus_fixture_sha256` covers Qdrant only — there's no machinery to detect that today's Tavily would contradict the recorded synthesis. Without a guardrail the eval silently bakes in time-stamped web content.

**Mitigation.** `record_cassettes_for_inputs()` and `just eval-record` force `config.enable_tavily_synthesis=False` for the duration of recording (override applied at the `Config` level before `_build_deps` constructs the LLM client; restored on exit). Replay reads the recorded cassettes with the same flag off — the LLM's recorded `tools_present` field never includes Tavily entries by construction. The eval validates the no-web synthesis path; the production-with-Tavily path is not eval-tested (see "Out of scope"). If a future eval row needs to exercise Tavily, lift this constraint into a per-row override and add a `RecordingTavilyClient` then.

This is the cheapest correct mitigation. Alternatives considered:
- `RecordingTavilyClient` + per-call cassettes: full fidelity, but doubles the wrapper surface for an optional production feature; not justified by current eval scope.
- Allow Tavily during recording with a "tools_present contains tavily" assertion gate on replay: punts staleness detection to operator vigilance; doesn't actually prevent the silent-staleness failure mode.

### Invariants list (cassette stability)

For cassettes to remain valid:

1. Prompt templates render deterministically given the same `InputContext` + `Config`.
2. `Config` values that flow into prompts are stable across runs (eval pins them via baseline metadata).
3. Tool-call result content from `get_post_mortem` / `search_corpus` is deterministic given a fixed corpus — already true, corpus is the JSONL fixture.
4. fastembed BM25 sparse encoding is deterministic given the same input text + model file — already true.
5. `Config.embedding_provider` and `Config.embed_model_id` match between record and replay. Drift produces 100 % embed-cassette miss (loud `NoCannedEmbeddingError`, not silent vector mismatch — the cassette key includes `model`). The recording metadata in `baseline.json` captures both fields so the intended setup is reviewable at a glance.

Violating any invariant produces non-reproducible cassette keys → cassette miss → operator sees `NoCannedResponseError` (LLM) or `NoCannedEmbeddingError` (dense / sparse) with the missing key in the error message.

## Migration sequence

One PR, eight commits with internal ordering. Each commit leaves `just test` and `just eval` green.

### Commit 1 — Cassette infrastructure
**Adds:** `slopmortem/evals/recording.py`, `slopmortem/evals/cassettes.py`, `tests/test_cassettes.py`, `tests/test_recording.py`.
**Modifies:**
- `slopmortem/llm/fake.py` — widen `canned` key to 3-tuple, strict lookup (no wildcard fallback).
- `slopmortem/llm/fake_embeddings.py` — optional `canned: Mapping[(text_hash, model), list[float]]` (strict on miss when supplied; sha-derived when `None`).
- All existing test sites that build the `canned` dict with 2-tuple keys: `tests/test_pipeline_e2e.py`, `tests/test_observe_redaction.py`, `tests/test_ingest_idempotency.py`, `tests/test_ingest_dry_run.py`, `tests/test_ingest_orchestration.py`, `tests/stages/test_synthesize.py`, `tests/stages/test_llm_rerank.py`, `tests/stages/test_facet_extract.py`. Each call site adds the third element (compute `prompt_hash` from the rendered prompt the test expects, or use a fixture-derived constant). `Mapping`'s key invariance under `basedpyright strict` makes this unavoidable in one commit.
**Note:** `NoCannedResponseError` message format updates to include `prompt_hash` in the missing-key tuple. Any test that greps the error message wording needs updating in the same commit. Plan should `grep -rn 'no canned response for' tests/` and adjust.
**Validation:** `just test` green. `just eval` still passes via canned. The runner's behavior is unchanged; only test scaffolding and `FakeLLMClient`/`FakeEmbeddingClient` shape change.

### Commit 2 — Corpus fixture machinery
**Adds:** `slopmortem/evals/corpus_fixture.py`, `tests/evals/test_corpus_fixture.py`, `tests/fixtures/corpus_fixture_inputs.yml`.
**Validation:** `just test` green (new tests run under `requires_qdrant`). Eval untouched.

### Commit 3 — Recording helper + ephemeral Qdrant
**Adds:** `slopmortem/evals/qdrant_setup.py` (the `setup_ephemeral_qdrant()` async context manager, decided at spec time — see "Open questions"), `slopmortem/evals/recording_helper.py`, `tests/evals/test_recording_helper.py`.
**Implementation note:** `record_cassettes_for_inputs()` constructs its `Config` with `enable_tavily_synthesis=False` (override applied before `_build_deps`, restored on exit) — see Risk 6. Test list covers: helper records expected scope dirs, helper restores Tavily flag on exit even when `run_query` raises.
**Validation:** `just test` green. Helper callable but unused by the rest of the repo.

### Commit 4 — Justfile + runner argparse (no behavior change)
**Adds:** `.gitattributes` containing the single line `tests/fixtures/corpus_fixture.jsonl filter=lfs diff=lfs merge=lfs -text`. Lands here (before commit 5 generates the JSONL) so the file is captured as LFS on first add.
**Modifies:**
- `justfile` — rewire the existing `eval-record` recipe (currently `--live --record`, see `justfile:24-25`) to drive the new recording flow; add a new `eval-record-corpus` target.
- `flake.nix` — add `git-lfs` to the dev shell `packages` list. Verify with `nix develop -c which git-lfs`.
- `slopmortem/evals/runner.py` — add `--scope` arg (recording + replay symmetric); add `--max-cost-usd <float>` (default `2.0`, only consulted in record mode); wire `--record` to call `record_cassettes_for_inputs(..., max_cost_usd=args.max_cost_usd)` instead of the deferred-stub message.
- `tests/test_eval_runner.py` — `test_runner_record_flag_is_deferred` asserts `--record` exits 0 with a "deferred" message. Rewrite it to assert real recording behavior under `RUN_LIVE`-gated patching (or split into a `requires_live` test plus a unit test that checks argparse wires `--record` to the new entry point). The "deferred" assertion is no longer truthful after this commit.
**Note:** the existing `eval` justfile comment on line 19 already (incorrectly) says "Default eval runs against cassettes via FakeLLMClient + FakeEmbeddingClient". That comment is currently a lie. Do NOT correct it here — defer the comment fix to commit 6 (when the runner default actually flips). Editing the comment in commit 4 would create a false-precondition window between commits 4 and 6 where the comment claims cassettes but the runner still uses canned.
**Validation:** `just test` green. `just eval` still uses canned by default. `just eval-record` would now write cassettes if invoked, but default replay path doesn't read them yet.

### Commit 5 — Generate fixtures (operator runs once)
**Preconditions (one-time per machine):**
- `git lfs install` (idempotent; the flake.nix change in commit 4 makes `git-lfs` available in the dev shell).
- `.gitattributes` from commit 4 is in place so the JSONL is captured as LFS on first add.
- `slopmortem embed-prefetch` (downloads the nomic dense + BM25 sparse fastembed models, ~700 MB combined; idempotent — re-runs are no-ops when the cache is warm). Without this, the first `eval-record-corpus` run pauses on a cold-start HuggingFace download mid-flow.

**Operator runs:**
```bash
docker compose up -d qdrant
slopmortem embed-prefetch              # one-time fastembed cache warm
RUN_LIVE=1 just eval-record-corpus    # ~$0.30-$1 under fastembed default; ~$3-5 if embedding_provider=openai
RUN_LIVE=1 just eval-record           # ~$0.50-$1, ceiling --max-cost-usd=2.0 (LLM-side only)
```
**Adds:** `tests/fixtures/corpus_fixture.jsonl` (~1.5MB, stored via LFS — `git diff` shows the LFS pointer, not the float arrays), `tests/fixtures/cassettes/evals/<row_id>/*.json` (10 dirs, ~70 files; regular git), updated `tests/evals/baseline.json` (v2).
**Validation:** `git lfs ls-files` shows the JSONL is tracked by LFS; cassette files appear as regular git diffs; operator spot-checks ~5 random `request_debug.prompt_preview` fields.

### Commit 6 — Switch runner default to cassettes
**Modifies:** `slopmortem/evals/runner.py`:
- Replace `_build_canned()` and helpers with cassette loading.
- Replace `_EvalCorpus` with `setup_ephemeral_qdrant`.
- Replace `FakeEmbeddingClient(model=...)` with canned-aware version (dense + sparse). The dense model id reads from `Config.embed_model_id` rather than the removed `_DETERMINISTIC_EMBED_MODEL` literal — this keeps the runner aligned with whatever provider the operator recorded under (`nomic-ai/nomic-embed-text-v1.5` for fastembed default, `text-embedding-3-small` for the OpenAI opt-in).
- Add SHA mismatch check (warn-only).
- Bump `_BASELINE_VERSION` from `1` to `2`.
- `_serialize_results(...)` now takes `corpus_fixture_sha256: str` and `recording_metadata: dict` and writes `{"version": 2, "corpus_fixture_sha256": ..., "recording_metadata": ..., "rows": ...}`.
- `_diff_against_baseline(...)` reads v1 and v2 transparently (v1: skip SHA check; v2: emit WARN on mismatch). Tolerates v1→v2 upgrade.
- `--write-baseline` preserves v2 metadata when present and only refreshes the rows it produced this run; with `--scope` it merges new row entries into the existing v2 baseline rather than clobbering.
- Import `NoCannedResponseError` (and `NoCannedEmbeddingError`); pre-check `cassette_dir.exists() and any(cassette_dir.iterdir())` per row, log `FAIL <row_id>: no cassettes` and continue if not; wrap `await run_query(...)` in `try/except (NoCannedResponseError, NoCannedEmbeddingError)`, log `FAIL <row_id>: cassette miss`, mark regression, continue. Both paths yield a sentinel result so `_diff_against_baseline` shows the row as failed in the per-row output.
**Modifies:** `justfile` — update the comment on line 19 to accurately reflect cassette-default behavior (deferred from commit 4).
**Removes:** `_build_canned`, `_facet_extract_payload`, `_rerank_payload`, `_synthesis_payload`, `_payload`, `_candidate`, `_facets`, `_DETERMINISTIC_*_MODEL`, `_EvalCorpus`, `_no_op_sparse_encoder`.
**Adds:** `tests/evals/test_runner_replay.py`, `tests/evals/test_fixtures/`. Test list must cover: passes with recorded cassettes, fails on missing cassette dir, fails on key miss (LLM and embedding, dense and sparse), warns on SHA mismatch, **v1 baseline upgrades cleanly to v2 on next `--write-baseline`**, **v2 round-trip preserves `corpus_fixture_sha256` + `recording_metadata` (including `embedding_provider`)**, `--scope` filtering applies to row loop and to baseline merge, unknown scope fails loudly, switching `Config.embed_model_id` between record and replay produces a `NoCannedEmbeddingError` cassette miss (not a silent vector mismatch).
**Validation:** `just test` green. `just eval` passes against committed cassettes from commit 5.

### Commit 7 — Migrate the e2e test
**Operator runs (before commit):** `record_cassettes_for_inputs()` with the e2e test's input → cassettes appear under `tests/fixtures/cassettes/e2e/test_full_pipeline_with_fake_clients/`.
**Modifies:** `tests/test_pipeline_e2e.py` — `test_full_pipeline_with_fake_clients` migrates to cassettes + ephemeral Qdrant + `requires_qdrant` marker. Other two tests unchanged.
**Validation:** `just test` green including the migrated e2e test running offline.

### Commit 8 — Documentation
**Modifies:** `README.md` or new `docs/cassettes.md` — section "Recording cassettes for custom tests" with the marketplace-scrap walkthrough. Onboarding section calls out the LFS dependency: contributors clone with `git lfs` available (the flake's dev shell handles this automatically; non-Nix contributors install it manually). CI checkout step needs `lfs: true` (e.g. `actions/checkout@v4` with `lfs: true` for GitHub Actions).
**Modifies:** `slopmortem/evals/runner.py` docstring — update mode list to reflect cassette-default behavior.
**Adds:** `tests/fixtures/cassettes/custom/.gitkeep`.

### Rollback strategy

If commit 6 (the behavior switch) breaks CI, the rollback is a single revert — commits 1–5 are pure additions and remain harmless. Cassette infrastructure stays available for fixing forward.

If commits 1–5 reveal a design flaw, those revert cleanly because nothing else depends on them.

Order optimized so each commit is independently reviewable, the eval stays green at every checkpoint, real-API spend is concentrated in commit 5 (one operator session), and the behavioral switch (commit 6) is atomic and revertable.

## Testing strategy

### New unit tests (pure Python, no Qdrant)

- `tests/test_cassettes.py` — key derivation stability, key changes on prompt drift, system/prompt separator works, `template_sha` changes on tools/schema edit (B2), round-trip serialization for LLM + dense embedding + sparse embedding cassettes, schema version rejection, malformed-JSON rejection, duplicate-key detection, dim mismatch, `_slugify_model` covers `/`, `:beta`, `:nitro` (F23). Test sites that build `canned` dicts now use 3-tuple keys (M16); strict no-fallback lookup raises on miss (B4).
- `tests/test_recording.py` — writes cassette on success, skips on inner error, uses full hash in filename, overwrites same-key, includes `request_debug` metadata. Covers all three wrappers (LLM, dense embedding, sparse). Includes a batch round-trip: 3-text `embed()` produces 3 cassette files; replay through `FakeEmbeddingClient.embed([t1,t2,t3])` returns the vectors in input order with `cost_usd=0.0, n_tokens=0` (G8); a single cassette is reused when the same text appears in two batches. Cost ceiling test (F20): `RecordingLLMClient(max_cost_usd=0.50)` with mocked inner returning `cost_usd=0.30` per call — third call raises `RecordingBudgetExceededError`, no cassette written for that call, accumulator carries previous calls' cost.

### New integration tests (require Qdrant)

- `tests/evals/test_corpus_fixture.py` — dump-then-restore yields same query results, SHA stable, SHA changes on edit.
- `tests/evals/test_runner_replay.py` — passes with recorded cassettes, fails on missing cassette dir, fails on cassette key miss (LLM and embedding), per-row continuation after `NoCannedResponseError` (G12), warns on SHA mismatch, v1 baseline skips SHA check, v1→v2 baseline upgrade on `--write-baseline` (G11), v2 round-trip preserves `corpus_fixture_sha256` + `recording_metadata`, `--scope` filtering applies to row loop and to baseline merge (G10), unknown scope fails loudly.
- `tests/evals/test_recording_helper.py` — uses `FakeLLMClient` as inner; verifies scope dir creation, tmp cleanup on failure, two-step atomic swap (B5: process killed between replaces leaves `.old` recoverable, no half-populated canonical dir), unique tmp_dir suffix (`{pid}.{uuid}.recording`) and 24h sweep of stale `.recording` dirs (F21), atomic swap removes leftover cassettes.

### Modified tests

- `tests/test_pipeline_e2e.py::test_full_pipeline_with_fake_clients` — migrated to cassettes (`requires_qdrant`).
- `tests/test_pipeline_e2e.py::test_run_query_records_budget_exceeded` — unchanged (canned).
- `tests/test_pipeline_e2e.py::test_ctrl_c_cancels_in_flight` — unchanged (canned).
- `tests/test_eval_assertions.py` — unchanged.
- All `tests/stages/*`, `tests/corpus/*`, `tests/smoke/*` — unchanged.

### CI behavior

- `just test` runs the full suite; cassette unit tests run as fast as today.
- `requires_qdrant` tests need `docker compose up -d qdrant` (already a CI requirement).
- No additional API keys required — recording tests use `FakeLLMClient` as the "inner" client.
- `RUN_LIVE` gating prevents accidental real-API calls in CI.

### Coverage targets

- `cassettes.py`: 100% (pure functions).
- `recording.py`: 100% (small, every error path matters).
- `corpus_fixture.py`: round-trip + SHA fully covered; Qdrant-specific code via integration tests.
- `recording_helper.py`: 100% via `FakeLLMClient`-driven tests.
- `runner.py` replay path: integration tests with real Qdrant + canned cassettes; existing diff/baseline tests preserved.

## Pros / cons / why we chose this

### Cassette key shape: `(template_sha, model, prompt_hash)` — chosen
- **Pros:** stable across runs given deterministic prompts; collisions effectively zero with 16-char hash; short enough for filenames; identical mechanism for record and replay.
- **Cons:** filename length ~60 chars (template + model + 16 hex). Acceptable.
- **Alternatives rejected:** full HTTP request hashing (vcrpy-style) — too tight to a specific HTTP shape, breaks across SDK upgrades. Hashing the entire request including tools/response_format — adds churn from non-semantic changes.

### Cassette layout: per-call JSON files in per-scope directories — chosen
- **Pros:** clean per-call diffs in PRs; filenames are a human-readable inventory; trivial to delete/regenerate one cassette; per-scope atomic-swap guarantees clean state.
- **Cons:** ~70 files in `evals/`; embedding cassettes duplicated across scopes when same text appears.
- **Alternatives rejected:** JSONL per row — re-recording one fan-out branch rewrites the whole file (noisy diff). SQLite — opaque diffs, tooling overhead, overkill for ~100 entries.

### Corpus storage: JSONL fixture, ephemeral Qdrant collection per run — chosen
- **Pros:** text-readable payload diffs; no Qdrant snapshot tooling; matches the existing `requires_qdrant` test pattern; simple atomicity (one file replacement); restored deterministically per run.
- **Cons:** ~1.5MB committed file; vector arrays opaque in PR diffs (acceptable — SHA check catches intentional changes).
- **Alternatives rejected:** Qdrant snapshot binaries (opaque, tooling-dependent). Re-ingest at eval start (doubles cassette surface to ~90 ingest cassettes; slow). Hand-curated tiny corpus (still needs real embeddings).

### Tool-call invariant: SHA check + warn on corpus drift — chosen
- **Pros:** detection is automatic, not silent; cheapest correct mitigation; doesn't reduce eval fidelity; operator-controlled response.
- **Cons:** stale cassettes still pass the eval until operator re-records. Acceptable — a warning, not a defect.
- **Alternatives rejected:** disable tools in eval (loses fidelity). Capture intermediate tool round-trips (significant complexity, eval doesn't need it).

### Cassette miss = regression, not crash — chosen
- **Pros:** operator gets the full picture in one run; per-row recovery via `--scope`; existing `NoCannedResponseError` already carries the missing key.
- **Cons:** harder to spot a single broken cassette in a sea of regressions. Acceptable — first run after re-recording is silent.
- **Alternatives rejected:** crash on first miss (masks subsequent issues). Auto-record on miss (silent change of fixture; no review opportunity).

### Three-layer reusable surface — chosen
- **Pros:** covers eval needs, custom-test needs, and ad-hoc exploration with one machinery; clean separation between "infrastructure" (Layer 1), "convenience" (Layer 2), "opinionated CLI" (Layer 3); no flag bloat on `eval-record`.
- **Cons:** more public API surface than a single CLI command would expose.
- **Alternatives rejected:** `--output-dir` flag on `eval-record` (muddies eval contract with custom-test contract). Single-CLI design (forces every cassette use case through `just`-shaped invocation).

### Atomic per-scope swap (overwrite, no `.bak`) — chosen
- **Pros:** clean state guarantee; git provides the audit log; recovery via `git checkout` is the standard developer workflow.
- **Cons:** easy to lose work if you don't review the diff before staging. Mitigated by the spec's operator-discipline note.
- **Alternatives rejected:** `.bak` directories (ambiguous state, gitignore decisions, disk creep).

## Out of scope

- `make` target (we use `just`).
- Cassettes for `tests/stages/*` (canned remains correct for those).
- Cassettes for `tests/smoke/*` (smoke runs against real API by design).
- Pytest plugin that auto-records on first run (overcomplicates the test author's mental model — explicit is better).
- Multi-region cassette consistency (Anthropic's eventual-consistency cache behavior). Not relevant once cassettes are committed.
- Cassette compression (~1MB total uncompressed, fine).
- Cassette migration from v1 schema in the future — handled when v2 ships, not pre-emptively.
- Tavily-driven synthesis fidelity in eval (`enable_tavily_synthesis=True`). Recording forces this flag off; the eval validates the no-web synthesis path only. If a future eval row needs Tavily exercised, add a `RecordingTavilyClient` then; see Risk 6.

## Open questions for plan-writing

These don't block the spec but need answers during plan-writing:

1. **Prompt template audit.** Confirm `slopmortem/llm/prompts/*.j2` are deterministic. Document the audit result in the plan.
2. **`recording_helper.py` baseline-write behavior.** Layer 2 helper shouldn't write `baseline.json` (Layer 3 owns that). Confirm wiring in the plan.

### Resolved during spec review

3. **`FakeEmbeddingClient` canned key shape.** Resolved: keys on `(text_hash, model)` to match the cassette key. Lookup chain is `text → sha256 → key → vector`. Symmetric with cassette files. Avoids storing raw text in the canned dict.
4. **`setup_ephemeral_qdrant()` location.** Resolved: lives in `slopmortem/evals/qdrant_setup.py` (its own module). Avoids the helper-to-runner import edge that would result from co-locating it in `runner.py`.

## Spec consistency check

Run before merging the implementation:

```bash
# Cassette schema version must be 1 everywhere we write
grep -nE 'schema_version' slopmortem/evals/recording.py slopmortem/evals/cassettes.py slopmortem/evals/recording_helper.py

# Baseline schema version is 2 in the new write path
grep -nE '"version": 2|_BASELINE_VERSION = 2' slopmortem/evals/runner.py

# No truncated hash in filenames anywhere
grep -nrE 'prompt_hash\[.*:8\]|text_hash\[.*:8\]' slopmortem/ tests/

# No 2-tuple wildcard fallback in FakeLLMClient (B4 - dropped)
grep -nE 'len\(key\) == 2|2-tuple|wildcard' slopmortem/llm/fake.py    # MUST be empty

# pipeline.run_query exposes sparse_encoder (B1)
grep -nE 'sparse_encoder' slopmortem/pipeline.py                       # MUST match

# Recording lives under evals/, not llm/ (G14)
test ! -e slopmortem/llm/recording.py                                  # MUST not exist
test -e slopmortem/evals/recording.py                                  # MUST exist

# Slugifier handles all non-[A-Za-z0-9._-] (F23)
grep -nE '_slugify_model|re\.sub.*\[\^A-Za-z0-9' slopmortem/evals/cassettes.py

# tmp_dir uses pid+uuid suffix (F21)
grep -nE '\.recording.*\{.*pid.*uuid' slopmortem/evals/recording_helper.py

# Sparse cassette uses Qdrant/bm25 model id
grep -nE '"Qdrant/bm25"' slopmortem/evals/recording.py slopmortem/evals/cassettes.py

# baseline.json recording_metadata captures embedding_provider
grep -nE '"embedding_provider"' slopmortem/evals/runner.py             # MUST match

# runner reads dense embed model from Config, not a hardcoded literal
grep -nE '_DETERMINISTIC_EMBED_MODEL' slopmortem/evals/runner.py       # MUST be empty
```

All "MUST match" should resolve to expected hits; "MUST be empty" / "MUST not exist" should produce no output / nonzero exit.

---

## Review findings (2026-04-29, parallel multi-agent review)

10 reviewers + 5 validators. Findings below are validator-confirmed (file:line evidence in spec or code). Severity: **B** = blocks ship, **G** = gap to define before plan, **M** = migration error, **F** = footgun. Status flags: `[autofix]` no trade-off, just apply; `[discuss]` real design decision needed.

### Tier 1 — architecture (B)

- **B1 — Sparse encoder plumbing missing for replay.** `slopmortem/pipeline.py:84-93` `run_query` does not accept `sparse_encoder`; `slopmortem/stages/retrieve.py:74-77` lazy-imports the production fastembed when none is passed. Spec line 155 declares `pipeline.py` "NOT touched" — wrong. Replay silently loads real fastembed every row. Also: line 497 says ~30 MB; `retrieve.py:67` docstring says ~150 MB (5× error in Risk 5 analysis). `[autofix]` thread `sparse_encoder` through `run_query` → `retrieve()`; record per-query sparse cassettes keyed `(text_hash, "Qdrant/bm25")`; remove `pipeline.py` from "NOT touched"; correct the 30 MB → 150 MB number.
- **B2 — Cassette key omits tool list, `response_format`, taxonomy.** Line 252 hashes only `(system + \x1f + prompt)`. `slopmortem/stages/synthesize.py:101-112` passes `tools=synthesis_tools(config)` AND a `response_format`; `slopmortem/llm/prompts/__init__.py:21` injects `taxonomy` as a Jinja global without folding into `template_sha`. Editing tool descriptions, the `Synthesis` Pydantic schema, or `corpus/taxonomy.yml` changes model behavior with zero cache invalidation. `[autofix]` add `tools_sha` and `schema_sha` dimensions to the key (or fold both into `template_sha` along with `taxonomy.yml` SHA); document in §"Cassette key derivation".
- **B3 — Tavily live-web tools are unfixtured.** `slopmortem/llm/tools.py:80-131` registers `tavily_search` / `tavily_extract` when `config.enable_tavily_synthesis=True`; results bake into recorded final text. `corpus_fixture_sha256` covers only Qdrant. Cassettes silently encode whatever the live web returned at record time. `[discuss]` two options: (a) assert `enable_tavily_synthesis=False` during recording (simple, loses fidelity); (b) capture per-call Tavily round-trips (more complex, full fidelity).
- **B4 — 2-tuple wildcard fallback violates spec's "loud miss" invariant.** Lines 44/144 lookup tries 3-tuple, falls back to 2-tuple. With both shapes co-existing on the same `(template_sha, model)` (which the migration window 1→6 produces), a real 3-tuple miss silently resolves to the wildcard 2-tuple — the exact silent fail-open line 91 forbids. `[autofix]` drop the 2-tuple fallback; in commit 1 update all 2-tuple call sites to 3-tuple at the same time.
- **B5 — `os.replace(tmp_dir, real_dir)` is NOT atomic on Linux for non-empty targets.** Lines 290-291 sequence: `rmtree(real_dir, ignore_errors=True); os.replace(tmp_dir, real_dir)`. POSIX `rename(2)` requires empty dest. SIGKILL between rmtree and replace destroys committed cassettes. The spec's atomicity claim doesn't hold. `[autofix]` two-step rename: `os.replace(real_dir, real_dir + ".old"); os.replace(tmp_dir, real_dir); shutil.rmtree(real_dir + ".old")`.
- **B6 — Spec contradicts itself on Qdrant startup sweep.** Line 376: *"No startup sweep — the per-pid prefix plus the try/finally drop is the only cleanup."* Line 490 (Risk 4): *"On entry, the helper sweeps only collections whose embedded pid is no longer running."* `[autofix]` pick one; reconcile lines 376/445/490/311.
- **B7 — `os.kill(pid, 0)` cannot detect pid reuse.** Returns success for any live process holding that pid. Recycled pids on long-lived hosts preserve leaked collections forever. `[discuss]` three options: (a) drop the sweep entirely, rely solely on `try/finally`; (b) time-based GC ("delete collections older than 24 h"); (c) embed `PYTEST_XDIST_WORKER` + a per-session UUID instead of pid.

### Tier 2 — spec gaps (G)

- **G8 — Embedding batch atomicity undefined.** `slopmortem/llm/openai_embeddings.py:74-90` is N-texts-in / N-vectors-out / single roundtrip / single `cost_usd` / single `n_tokens`. Spec line 46 says "writes one cassette per call" (singular); cassette schema lines 205-221 keys on a single `text_hash`. `[discuss]` design call: (a) per-text cassette (current schema; spec must specify N→N split + cost/token allocation rule + how `FakeEmbeddingClient` reassembles a batched response); (b) batch cassette schema (key on a hash of the input list).
- **G9 — `FakeEmbeddingClient` miss policy unspecified.** Line 145 says "Add optional `canned`. Default behavior unchanged." Silent on whether `canned` present + miss raises (loud) or falls through to sha-derived (silent fail-open). `[autofix]` specify: `canned is not None` ⇒ strict; raise `NoCannedEmbeddingError` on miss; no sha fallthrough. `canned=None` ⇒ today's sha behavior. Symmetric with LLM cassette rule.
- **G10 — `--scope` is recording-only; no replay filter.** Lines 276/285/313 add `--scope` to the recording flow; replay flow (lines 320-349) never uses it. Test list line 580 expects "`--scope` filtering" tests. `[autofix]` make `--scope` symmetric: applies to the row loop in replay (skip rows that don't match) and to `--write-baseline` (update only that row's entry).
- **G11 — Baseline v2 round-trip incomplete.** `slopmortem/evals/runner.py:123` `_BASELINE_VERSION = 1`; `_serialize_results` (602-604) writes only `{"version": 1, "rows": ...}`; `_diff_against_baseline` (528) reads only `rows`; `--write-baseline` (694-697) clobbers v2 metadata. `[autofix]` commit 6 modification list must add: bump `_BASELINE_VERSION = 2`; `_serialize_results` accepts `corpus_fixture_sha256` + `recording_metadata`; `--write-baseline` preserves v2 fields; add unit test for v1→v2 upgrade and v2 roundtrip.
- **G12 — Per-row `NoCannedResponseError` continuation unimplemented.** `runner.py:464-474` row loop calls `await run_query(...)` with no try/except; `NoCannedResponseError` not imported. Spec's "tell me everything wrong in one run" promise (line 359) is not implemented. `[autofix]` commit 6: import `NoCannedResponseError`; before each row check `cassette_dir.exists()` and `any(cassette_dir.iterdir())`; wrap `run_query` in `try/except NoCannedResponseError`; record sentinel result + emit `FAIL <row_id>: …`.
- **G13 — Layer 2 helper signature missing `corpus_fixture_path`.** Lines 397-408: `record_cassettes_for_inputs(inputs, output_dir)`. Line 408 says it "handles ephemeral Qdrant setup" but `setup_ephemeral_qdrant(fixture_path, …)` requires a fixture path — nowhere in the signature. `[discuss]` add `corpus_fixture_path: Path` (no default ⇒ explicit) OR default to canonical eval fixture (couples custom tests to eval seed corpus).
- **G14 — Layering violation: `slopmortem/llm/recording.py` must import from `slopmortem/evals/cassettes.py`.** Today's import direction is one-way `evals → llm`. Line 260 forces the new edge `llm → evals`. Diagram lines 161-169 omits this edge. Line 150 also re-exports `RecordingLLMClient` from `slopmortem/llm/__init__.py` (currently a docstring), leaking test infra into the production LLM surface. `[autofix]` move `recording.py` to `slopmortem/evals/recording.py`; drop the `slopmortem/llm/__init__.py` re-export; update line 124 component table and line 161-169 diagram.
- **G15 — `setup_ephemeral_qdrant()` location is left as an open question.** Line 658 punts on `runner.py` vs `qdrant_setup.py`. `[autofix]` decide now: put it in `slopmortem/evals/qdrant_setup.py` (or co-locate with `corpus_fixture.py`'s `restore_jsonl_to_collection`). `runner.py` and `recording_helper.py` both import it; no helper-to-runner edge.

### Tier 3 — migration sequence errors (M)

- **M16 — Commit 1 type-broken under `basedpyright strict`.** ≥9 test sites annotate `Mapping[tuple[str, str], FakeResponse]` (e.g. `tests/test_pipeline_e2e.py:140`, `tests/test_observe_redaction.py:154`, `tests/test_ingest_idempotency.py:40`, `tests/test_ingest_dry_run.py:38`, `tests/test_ingest_orchestration.py:67,259`, `tests/stages/test_synthesize.py:101`, `tests/stages/test_llm_rerank.py:89`, `tests/stages/test_facet_extract.py:30`). Widening `canned`'s key breaks `Mapping`'s key-invariance. `[autofix]` commit 1 modifications list must include all these test files; "no production code paths altered" claim must be deleted.
- **M17 — `test_runner_record_flag_is_deferred` breaks at commit 4.** `tests/test_eval_runner.py:209-227` asserts `--record` exits 0 and prints "deferred". Commit 4 changes that. `[autofix]` add the test file to commit 4's modification list; rewrite the test to assert real recording behavior (gated under `RUN_LIVE`).
- **M18 — `eval-record` recipe already exists.** `justfile:24-25` already invokes `--live --record`. Spec frames it as new. `[autofix]` rewrite spec lines 532-533 to "rewire existing `eval-record` recipe"; verify what (if anything) currently invokes it.

### Tier 4 — operational footguns (F)

- **F19 — Tool-loop cost/cache_* recorded as sum-over-turns.** `slopmortem/llm/openrouter.py:128-133` accumulates `cost_usd`, `cache_read`, `cache_write` across the loop. Cassette captures the sum; replay assertions reflect aggregate, not single-call, semantics. `[autofix]` document this in §"Risks and invariants" or in the cassette schema docstring.
- **F20 — No cost ceiling on `eval-record`.** Spec quotes "$0.50–$1 per full record" assuming ~10 rows × 7 calls. Tool-loop amplification (a synthesis call retrieving 3 docs ≈ 4× tokens) easily pushes toward $2–3. Models may upgrade. `[discuss]` add `--max-cost-usd` flag to `RecordingLLMClient`? abort or warn on hit? what default ceiling (2× estimate)?
- **F21 — `tmp_dir = <scope_dir>.recording/` is fixed-name → permanent landmines.** No pid/uuid suffix; crash leaves a hard-refusal until manual `rm -rf`. No startup sweep for orphans analogous to the Qdrant one. `[autofix]` suffix tmp_dir with `.{pid}.{uuid4().hex}.recording`; on entry sweep stale `*.recording` dirs older than N hours.
- **F22 — No Git LFS for the 1.5 MB regenerable JSONL.** No `.gitattributes` exists. Each `eval-record-corpus` rewrites the whole blob. `[discuss]` options: (a) add `.gitattributes` with LFS for `tests/fixtures/corpus_fixture.jsonl`; (b) store dense vectors as base64-packed float32 (4× smaller, opaque diffs by design); (c) accept history bloat (rare regen + small absolute size).
- **F23 — Filename slug rule incomplete.** Line 70 escapes only `/`. OpenRouter supports `:beta`, `:nitro`, `:free` suffixes; `:` is forbidden in Windows filenames. `[autofix]` change rule to: replace any non-`[A-Za-z0-9._-]` with `_` (single regex covers `/`, `:`, `@`, future surprises).

### Validator-rejected concerns (NOT issues)

- `\x1f` separator aliasing — control chars don't realistically appear in scraped post-mortem content.
- SHA mismatch warning-only "contradicts" "automatic, not silent" — semantically reconcilable; defensible design choice.
- Spec's grep guidance for `NoCannedResponseError` message — true that no test greps the literal phrase, but loose substring assertions survive any 3-tuple format change. Note in spec is inert, not a defect.
- `taxonomy.yml` impact on cache-hit *rate* — actually mitigated by `prompt_hash` since rendered prompt changes when taxonomy changes; the real concern (covered in B2) is that `template_sha` doesn't change, leaving directory layout stale.

