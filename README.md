# slopmortem

![demo](example.gif)

You give it a pitch, it finds dead startups that tried something similar.

`slopmortem` runs locally. LLM calls go through OpenRouter, which sends them to Anthropic's Sonnet and Haiku by default. Embeddings run locally via fastembed (ONNX); flip to OpenAI if you'd rather. Qdrant runs in Docker.

Pipeline diagram, query/ingest flow, and source layout live in [`docs/architecture.md`](docs/architecture.md).

Reports lead with a "Top risks across all comparables" section: pure-Python clustering of the per-candidate `lessons_for_input` lists by token-set similarity, sorted by how many comparables raised each one. Then the per-candidate post-mortems, then a cost/latency/trace footer.

## Running it

Dev shell is a Nix flake. With direnv: `direnv allow` and the shell loads on `cd`. Without: `nix develop`. The shellHook calls `uv venv` + `uv sync --frozen`, so Python is ready by the time the prompt returns. Then `just` for the rest.

Secrets go in `.env` (gitignored). `just init-env` walks the prompts: `OPENROUTER_API_KEY` is required; `OPENAI_API_KEY` only if you flip `embedding_provider` to OpenAI; `TAVILY_API_KEY` only if you enable Tavily; `LMNR_PROJECT_API_KEY` only if `enable_tracing = true`. The recipe is idempotent, so re-run it any time and press Enter on keys you already have set. Knobs live in `slopmortem.toml` with comments.

First-run sequence:

```
direnv allow                         # or: nix develop
just init-env                        # interactive — fill OPENROUTER_API_KEY, skip the rest
docker compose up -d qdrant          # Qdrant on :6333
slopmortem embed-prefetch            # one-time ~550 MB ONNX download
just ingest                          # 50 entries with all enrichers; or `just ingest-all`
just query "your pitch here"         # ~$0.40 per call, run whenever; or `just query-debug` to skip rerank+synth
```

Ingest picks up curated + HN automatically. Add `--crunchbase-csv PATH` for a Crunchbase dump. The repo ships the 2015 `notpeter/crunchbase-data` mirror as a git submodule under `external/crunchbase-data/`. Run `git submodule update --init` once to fetch it, then `just crunchbase` to produce a closed-only slice (~6.2K rows at `data/crunchbase/companies-closed.csv`, tracked in this repo) and point `--crunchbase-csv` at it. `--enrich-wayback` chases 404s through the Wayback Machine — recommended alongside the Crunchbase slice, since most 2015 dead-startup homepages are long gone. `--tavily-enrich` fills missing context from Tavily search. `--dry-run` counts without writing; `--force` bypasses the per-source skip key.

<details>
<summary><b>Maintenance corners</b></summary>

`slopmortem ingest --reconcile` patches drift between the journal, the markdown tree, and Qdrant. `slopmortem ingest --reclassify` re-runs the slop classifier against the quarantine tree and routes survivors back through entity resolution. `slopmortem ingest --list-review` prints the entity-resolution review queue (tier-2 ambiguous pairs that landed in the calibration band).

Storage defaults to `./post_mortems/{raw,canonical,quarantine}/` with the merge journal at `./journal.sqlite` next to the root. Override with `--post-mortems-root` or the `POST_MORTEMS_ROOT` / `MERGE_JOURNAL_PATH` env vars. The fastembed model lands wherever fastembed defaults unless you point `embed_cache_dir` somewhere in `slopmortem.toml`.

</details>

<details>
<summary><b>Configuration</b></summary>

`slopmortem.toml` (tracked) holds the documented defaults; every field has a comment. Don't edit it for personal tweaks. Drop a `slopmortem.local.toml` next to it with only the keys you want to override — the loader reads both from the current working directory and `.local.toml` wins. `.local.toml` is gitignored. Env vars (and `.env`) also override the tracked defaults, but `.local.toml` wins over env too, so it's the one knob to reach for.

</details>

<details>
<summary><b>Embedding provider</b></summary>

fastembed is the default because it runs offline, costs nothing, and means CI doesn't need an OpenAI key. The model is `nomic-ai/nomic-embed-text-v1.5`, 768d. Switch to OpenAI in `slopmortem.toml`:

```toml
embedding_provider = "openai"
embed_model_id = "text-embedding-3-small"   # or text-embedding-3-large
```

Bringing a different model? Add a row to `EMBED_DIMS` in `slopmortem/llm/openai_embeddings.py`. Qdrant reads it to size the collection.

</details>

<details>
<summary><b>Cassettes &amp; replay</b></summary>

Every LLM and HTTP call made during tests or evals replays from `tests/fixtures/cassettes/` (pytest-recording, vcrpy underneath). That's why `just test` and `just eval` are free and offline — `FakeLLMClient` + `FakeEmbeddingClient` plus disk-backed cassettes for the rest. Cassettes get re-recorded on demand, not in CI, because each re-record hits live OpenRouter and costs real money.

`slopmortem replay <dataset>` is the runtime equivalent: it re-runs a saved JSONL of inputs through current code without re-burning the LLM bill, which is what you want when you're iterating on prompts.

</details>

<details>
<summary><b>Testing &amp; evals</b></summary>

Cassettes via pytest-recording, vcrpy underneath. No respx — both libraries patch the same httpx transport, and when they coexist you get fixture-order flakes that aren't local to whatever test is actually broken. One library is enough.

`just smoke-live` hits live OpenRouter on a manual trigger, roughly weekly. The point is to catch when an SDK, a model, or OpenRouter's routing layer silently shifts behavior. Everything else replays from disk.

The eval harness lives in `slopmortem/evals/`. `just eval` runs the seed dataset through the pipeline using `FakeLLMClient` + `FakeEmbeddingClient` against recorded cassettes; offline, deterministic, asserted against `tests/evals/baseline.json`. `just eval-record` re-records the cassettes against live OpenRouter + local fastembed under a `--max-cost-usd 2.0` ceiling. `just eval-record-corpus` regenerates the seed corpus fixture from `tests/fixtures/corpus_fixture_inputs.yml`; budget about $0.30–$1 with the default fastembed embedder. Both record commands cost real money, so they're manual triggers, not anything CI runs.

</details>

## Known limitations

- **Alias-graph dedup is K-bounded.** `QdrantCorpus.query` fetches alias edges only for the canonicals that survived into the top-`K_retrieve` set. If `A↔B↔C` are aliased and `B` was pruned upstream (recency, facet boost, RRF), the chain only collapses on the hops touching retrieved nodes — so `A` and `C` can surface as separate candidates instead of one component. Harmless when alias chains are ≤1 hop, which is the common case. Fix would be a transitive-closure pass over `fetch_aliases`; revisit if it shows up in real queries.
- **Chunk-to-parent over-fetch ratio assumes ~4 chunks/doc.** Qdrant over-fetches `K_retrieve * 4` chunks expecting them to collapse to ≥`K_retrieve` parents. Long post-mortems chunk into many more pieces and can silently under-fill the parent set. Re-tune the multiplier (or move to a parent-aware fetcher) before relying on `K_retrieve` as a hard floor on a real corpus.
- **LLM rerank cost is linear in `K_retrieve`.** Every candidate's summary goes into one prompt; doubling K doubles tokens. Fine at K=30; revisit (two-stage rerank, local cross-encoder, or tighter summaries) if K grows.

## Examples

Sample runs with pitch, rendered report, and Laminar trace live under [`docs/examples/`](docs/examples/).

## Design notes

Full spec is in [`docs/specs/2026-04-27-slopmortem-design.md`](docs/specs/2026-04-27-slopmortem-design.md). The pre-implementation punch list of contract bugs to close before code is in [`docs/specs/2026-04-28-design-spec-blockers.md`](docs/specs/2026-04-28-design-spec-blockers.md).
