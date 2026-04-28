# slopmortem

Tell it your startup idea, it finds dead ones that tried something similar.

The CLI is `slopmortem`. Runs on your machine, uses Anthropic, OpenAI, and a local Qdrant container.

## Architecture

```mermaid
flowchart TB
    User([you])

    User -->|slopmortem "my pitch"| CLI
    User -->|slopmortem ingest| CLI

    CLI[["CLI (typer + asyncio.run)"]]

    CLI --> Q
    CLI --> I

    subgraph Q[Query pipeline]
        direction TB
        Q1[facet_extract · Haiku]
        Q2[embed dense + sparse]
        Q3[retrieve · Qdrant RRF]
        Q4[llm_rerank · Sonnet]
        Q5[synthesize × N · Sonnet + tools]
        Q6[render markdown]
        Q1 --> Q2 --> Q3 --> Q4 --> Q5 --> Q6
    end

    subgraph I[Ingest pipeline]
        direction TB
        I1[fetch sources]
        I2[facet + summarize<br/>Anthropic Batches, 50% off]
        I3[embed]
        I4[entity resolution]
        I5[merge: journal → md → qdrant]
        I1 --> I2 --> I3 --> I4 --> I5
    end

    subgraph llm[llm/]
        LLMC[LLMClient · Anthropic SDK]
        Embed[EmbeddingClient · OpenAI 3-small]
        Tools[tools.py]
        Prices[(prices.yml)]
    end

    subgraph corpus[corpus/]
        Qdrant[(Qdrant · dense + sparse)]
        MD[(on-disk markdown)]
        Journal[(MergeJournal · sqlite WAL)]
        Sources[sources/ · curated, HN, Tavily]
    end

    subgraph cross[cross-cutting]
        Budget[budget.py]
        Trace[tracing.py · Laminar/OTel]
    end

    Q -.-> llm
    Q -.-> corpus
    I -.-> llm
    I -.-> corpus
    Q -.-> cross
    I -.-> cross

    LLMC --> ANT([Anthropic API])
    Embed --> OAI([OpenAI API])
    Sources --> Web([HN · curated URLs · Tavily])
    Trace --> LMNR([Laminar collector])
```

The CLI does one `asyncio.run` and that's it. Below it, every stage is `async def`. fastembed is CPU-bound so it hops onto a thread. The synthesis fan-out uses `asyncio.gather`. Each SDK gets one connection pool that lives for the whole invocation, which is the kind of thing you don't think matters until you watch six sequential LLM calls each pay the TLS handshake tax.

## Query flow

You type `slopmortem "we're building a marketplace for industrial scrap metal"`. Here's what runs.

1. **Facets.** Haiku reads the pitch and slaps structured fields on it: sector, business model, stage, that kind of thing. These narrow what we retrieve and feed the rerank rubric later on.
2. **Embeddings.** Dense via OpenAI `text-embedding-3-small`. Sparse via fastembed BM25. Two vectors per query, both cheap.
3. **Retrieve.** Qdrant runs three prefetches in parallel (dense, sparse, and one filtered by your facets), then fuses them server-side with Reciprocal Rank Fusion. Top 30 candidates come back. No HyDE, no query rewriting; we tried HyDE earlier and it kept biasing retrieval toward Haiku's favorite failure tropes ("ran out of runway", "scaled too fast"), which made every result look the same.
4. **Rerank.** One Sonnet call scores all 30 with a multi-perspective rubric. Output is a Pydantic struct via tool-use, so we don't regex over prose. Top 5 survive.
5. **Synthesize.** First call runs alone. That's deliberate, not a sequencing bug. It populates the prompt cache so the other four don't all race to write the same entry. Once it returns, the rest fire in parallel via `asyncio.gather`. Each writes one candidate report, and the model can call `get_post_mortem` or `search_corpus` mid-generation if it wants more context.
6. **Render.** Markdown to stdout. The footer carries cost, latency, and the trace ID, so when something looks off you can paste a Laminar link straight from your terminal.

A normal query lands somewhere around $0.45–0.80 and finishes in 10–20 seconds. Cap is $1.50; the budget tracker raises if you blow past it.

## Ingest flow

`slopmortem ingest` is the bulk path. Around 500 URLs from a curated YAML, HN's Algolia API, and Tavily if you opt in.

1. **Fetch.** Plain HTTP. trafilatura strips nav and cookie banners. A length floor drops the obviously empty pages.
2. **LLM fan-out.** Two calls per doc, one for facet extraction and one for the rerank summary. All ~1000 go up as a single Anthropic Message Batch at 50% off. The shared system block has a 1-hour cache TTL, and we fire one sync call right before submitting the batch so workers find the cache already populated instead of racing to write it. This matters because Anthropic's docs say cache hits inside a batch are "best-effort" and that range is 30% to 98% in practice, which is a wide enough range to wreck your cost estimate if you don't bias it on purpose.
3. **Embed.** Dense via OpenAI. Sparse on the local CPU. Cheap enough that I stopped worrying about it.
4. **Entity resolution.** Three tiers. Domain match first, then embedding similarity, then a Haiku tiebreaker for the actually ambiguous pairs. Mostly the point of all this is to stop "Crunchbase obituary + founder's farewell blog post" from showing up as two separate dead startups.
5. **Merge.** Journal flips the row to `pending`, markdown lands via `os.replace`, Qdrant gets upserted, then the journal flips to `complete`. If something dies in the middle (Ctrl-C, OOM, bad network, whatever), `slopmortem ingest --reconcile` walks the three stores and patches whatever drifted.

The initial 500-URL seed runs about $5. The cap is $10 because retries happen and I wanted slack. Steady-state on the HN feed is roughly $0.07/week, which is small enough that I stopped tracking it.

## What's where

```
slopmortem/
  cli.py                 # entry point — every command goes through asyncio.run
  pipeline.py            # query orchestration, async stage composition
  ingest.py              # ingest orchestration
  stages/                # one module per stage; every function is async def
  llm/
    client.py            # LLMClient Protocol + AnthropicSDKClient + FakeLLMClient
    embedding_client.py  # OpenAI + Fake variants
    tools.py             # ToolSpec, Pydantic → Anthropic schema conversion
    prices.yml           # source of truth for $$
    prompts/             # *.j2 templates with paired JSON Schemas
  corpus/
    sources/             # curated, hn_algolia, tavily
    qdrant.py            # hybrid retrieval (dense + sparse + facet RRF)
    merge.py             # MergeJournal (stdlib sqlite3, WAL, busy_timeout=5000)
    paths.py             # safe_path validation for raw/ and canonical/ trees
  tracing.py             # Laminar/OTel; loopback default
  budget.py              # per-invocation cost cap
  config.py
data/
  journal.sqlite         # merge journal
  raw/<source>/<id>.md   # one file per fetched source doc
  canonical/<id>.md      # one file per merged canonical entry
docs/specs/              # design spec + open issues
tests/
  cassettes/             # pytest-recording (vcrpy under the hood, no respx)
  fixtures/
  evals/
```

## Running it

`ANTHROPIC_API_KEY` and `OPENAI_API_KEY` go in `.env`. Tavily is optional. Qdrant runs in Docker.

```
uv sync
docker-compose up -d qdrant
slopmortem ingest --source curated   # ~$5, run this once
slopmortem "your pitch here"         # ~$0.50, run whenever
```

Two corners worth knowing about. `slopmortem ingest --reconcile` patches drift between the journal, the markdown tree, and Qdrant. `slopmortem replay --dataset <name>` re-runs a saved input through current code, which is what you actually want when you're iterating on prompts and trying not to reburn the LLM bill on the same examples.

## Testing

Cassettes via pytest-recording, with vcrpy underneath. I don't pair it with respx because both libraries patch the same httpx transport layer, and when they coexist you get fixture-order flakes that aren't local to the test that's broken. One library is enough.

`make smoke-live` runs against the real Anthropic API on a manual trigger, roughly weekly, mostly so I notice when an SDK or model update silently changes behavior. Everything else replays from disk.

## Design notes

Full spec is in [`docs/specs/2026-04-27-slopmortem-design.md`](docs/specs/2026-04-27-slopmortem-design.md). Open issues against the spec live in [`docs/specs/2026-04-28-design-review-issues.md`](docs/specs/2026-04-28-design-review-issues.md).
