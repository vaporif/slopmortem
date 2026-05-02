# Foundations: retrieval, embeddings, hybrid search, RAG

The mental model from scratch. Build up step by step; jargon comes later.

## The problem

You have a pile of documents. Someone hands you a query. You want to return
the documents most similar to that query, ranked.

That's the whole problem retrieval solves. The hard part: a computer has no
idea what "similar" means. You have to define it mechanically.

## Approach 1: lexical (keyword overlap)

Simplest definition of similar: "shares words with the query."

Query: *"AI-powered legal contract review"*. A doc that contains "AI",
"legal", "contract", "review" probably matches. A doc that contains none of
those words probably doesn't.

Smarter than just counting:

- Rare words ("aptamer") matter more than common ones ("the"). → **TF-IDF**
- Long documents have more word occurrences, so penalize length. → **BM25**

BM25 has been around since 1994 and is still the strongest non-neural
baseline. Fast, interpretable, no GPU needed.

**Where it fails:** vocabulary mismatch. Query says "shared workspace," doc
says "coworking" — zero overlap, zero score, even though they mean the same
thing. That is the dealbreaker for semantic queries.

## Approach 2: semantic (meaning overlap)

We want "shared workspace" and "coworking" to score high against each other.
To do that, you need a notion of *meaning*, not just *words*.

The trick: train a neural network to map any text to a point in
high-dimensional space (say, 1024 dimensions), such that texts with similar
meaning land near each other. That point is an **embedding**, and the vector
of 1024 numbers is the **dense vector**.

Now "similarity" = "geometric distance between two points." Specifically
**cosine similarity** (the angle between them).

The model that does the mapping is the **embedding model** — different from a
chat LLM, it only produces vectors and doesn't generate text.

**Where it fails:**

- Rare proper nouns. "Theranos" might land near "biotech startup" generically
  and miss its uniqueness.
- Out-of-distribution jargon. If the embedder never saw a term during
  training, it gets a fuzzy guess.
- Short queries dominated by one specific term. A query saying "Rust ORM"
  wants exact matches on those two tokens; an embedder might smear it toward
  "Rust web framework" or "Python ORM" because those are conceptually
  adjacent.

## The punchline: combine them

Lexical and semantic fail in *opposite* directions. Lexical loses to
paraphrase but nails exact terms. Semantic loses on rare exact terms but
handles paraphrase. So you run both and merge.

That's **hybrid retrieval**. Two systems each return their top 100. Then you
fuse the two ranked lists into one.

How do you merge? You can't just add scores — BM25 might output 4.2 while
cosine outputs 0.87, they're different scales. So you use **Reciprocal Rank
Fusion (RRF)**: throw away the scores, keep the *ranks*, score each item by
`1/(60+rank)`, sum across the two lists. Whatever's high-ranked in either
list bubbles up. Items high in *both* lists bubble up the most.

That's why "dense + sparse + RRF" is everywhere. It's the most boring,
robust hybrid setup.

## Vector databases

You can't compute cosine similarity against millions of vectors at query
time by brute force — that's millions of multiplications per query. So you
build an index that finds approximate nearest neighbors quickly. Examples:
Qdrant, Pinecone, Weaviate, Milvus, pgvector, FAISS.

You hand it your vectors at ingest time, it builds an index (usually HNSW, a
graph structure), and queries become millisecond operations.

Modern vector DBs support both dense and sparse vectors in the same
collection and run RRF fusion server-side, so a single round-trip does the
whole hybrid query.

## Where the LLM enters

Everything above is just *retrieval*. No LLM yet. Retrieval gives you, say,
the top 50 candidate documents.

Then a typical RAG pipeline does up to three more things, each LLM-powered:

1. **Rerank**: a stronger but slower model re-scores those 50 to pick the
   final 10. LLMs (or cross-encoders) read the query and each candidate
   together, give a relevance score. More accurate than embedding cosine
   alone, but you can only afford it on a small set, hence the
   retrieve-then-rerank shape.
2. **Synthesize**: feed the top 10 into an LLM with the query, ask it to
   write a narrative answer.
3. **Post-processing** (optional): another LLM pass to summarize, cluster,
   or transform the synthesized output.

This whole shape — *retrieve relevant docs, then have an LLM read them and
answer the question* — is **RAG (retrieval-augmented generation)**. The
"augmented" part is "we gave the LLM external context it didn't have in its
weights."

## The full stack at a glance

```
query
  ↓
[embed dense]   [encode sparse / BM25]
        ↓                    ↓
        └─→  vector DB: prefetch each, fuse via RRF, optional filters/boosts
                                 ↓
                       top ~50 candidates
                                 ↓
                       reranker (cross-encoder or LLM) → top 10
                                 ↓
                       LLM synthesizer → answer
```

Retrieval is the bottom two layers. Everything above is generation.

## What to internalize

- Lexical and semantic retrieval are complementary, not competing.
- Score scales differ across systems; rank-based fusion sidesteps that.
- ANN indexes trade a small recall hit for orders-of-magnitude speedup.
- Retrieval is a bigger deal than the LLM in most RAG systems. Most RAG
  failures are retrieval failures in disguise.
