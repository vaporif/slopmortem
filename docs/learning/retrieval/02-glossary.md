# Glossary: retrieval, vectors, hybrid, RAG

Terms organized by what they describe. Skim once, return as needed.

## Vector basics

- **Embedding**: a model-produced vector representing the meaning of text.
  Two similar texts → vectors close in space.
- **Dense vector**: hundreds–thousands of float dimensions, all populated.
  Captures *semantics*. Produced by an embedding model.
- **Sparse vector**: vocab-sized, mostly zeros. Each non-zero is a token
  weight. Captures *lexical* matches. BM25 produces sparse vectors.
- **Cosine similarity**: the angle between two vectors. Most embedding models
  are trained for this. Equivalent to dot product on length-normalized
  vectors.
- **Dot product**: sum of element-wise products. Faster than cosine if the
  vectors are already normalized.
- **BM25**: classical sparse-retrieval algorithm. Token-frequency weighted
  by rarity, normalized for document length. Predates neural embeddings by
  ~25 years; still strong for keyword-heavy queries.
- **TF-IDF**: BM25's simpler ancestor. Term frequency × inverse document
  frequency.

## Retrieval methods

- **Top-K retrieval**: "give me the K most similar items." K is a knob.
- **ANN (approximate nearest neighbor)**: finding "close" vectors fast over
  millions of items. Exact search is O(N); ANN trades a bit of recall for
  log-time speed.
- **HNSW**: the most-used ANN algorithm. Hierarchical navigable small-world
  graph. Used by Qdrant, Weaviate, Vespa, pgvector (with `hnsw` index), etc.
- **IVF (inverted file index)**: alternative ANN family. Coarse cluster +
  search-only-relevant-clusters. Used by FAISS.
- **PQ (product quantization)**: compress vectors by splitting into subvectors
  and quantizing each. Lower memory, slightly worse recall. Often paired
  with IVF (`IVF_PQ`).
- **Quantization**: representing floats with fewer bits — int8, binary.
  Memory savings; small recall hit.
- **Recall@K**: "did the right answer make it into the top K?" The metric
  retrieval mostly cares about — wrong-but-rare answers can be dropped by
  the reranker, but missing answers are gone forever.
- **Precision@K**: "of my K, how many were correct?"

## Hybrid + fusion

- **Hybrid retrieval**: running dense and sparse in parallel and combining
  results.
- **RRF (Reciprocal Rank Fusion)**: rank-based merge. Each system gives a
  ranked list; RRF scores each item by `1/(k + rank)` summed across systems.
  `k=60` is the canonical default. Robust because it ignores raw score
  scales.
- **Score normalization**: alternative to RRF — try to make dense and sparse
  scores comparable, then sum. Brittle. RRF is the safer default.
- **Late fusion** vs **early fusion**: late = retrieve separately, merge
  results (RRF). Early = build one combined index. Late is more flexible.
- **Reranking**: after retrieval gets you the top 50, a stronger but slower
  model re-scores them to pick the final 10.
- **Cross-encoder**: encoder that takes (query, doc) *together* and outputs
  a relevance score. More accurate than bi-encoder cosine, much slower —
  hence used as reranker, not retriever.
- **Bi-encoder**: encoder that produces query and doc vectors *separately*.
  Fast (vectors precomputed at ingest); less accurate. The standard
  retrieval setup.

## Indexing

- **Inverted index**: data structure mapping `term → list of docs containing
  it`. The thing that makes BM25 fast. Lucene/Elasticsearch are built on
  this.
- **Tokenization**: splitting text into tokens. Classical IR uses words +
  stemming + stopwords; transformers use subword tokens (BPE / WordPiece /
  SentencePiece).
- **Stemming / lemmatization**: collapsing inflections ("running" →
  "run"). Useful for BM25, irrelevant for transformers.
- **Stopwords**: common words ("the", "a") dropped from indexing.
- **Chunking**: splitting long documents into smaller passages so they fit
  in context and so retrieval is more precise. Each chunk gets its own
  vector.
- **Parent / child collapse**: retrieve at chunk granularity, collapse to
  parent doc to dedupe. Lets you over-fetch and pick the best hit per
  document.

## RAG pipeline

- **RAG (retrieval-augmented generation)**: retrieve relevant docs → stuff
  into LLM context → generate.
- **Context window**: how many tokens an LLM can read at once. Retrieved
  context has to fit.
- **Query rewriting**: turning a user question into a better retrieval query.
  Patterns: HyDE (generate a hypothetical answer, embed *that*),
  multi-query (run several variants), decomposition (break into
  sub-queries).
- **Grounding / citation**: making the LLM tell you which retrieved docs
  it used.
- **Hallucination**: LLM generates a plausible-but-false answer. Often
  caused by retrieval miss — no relevant doc was found, model fills in
  from its weights.
- **Lost-in-the-middle**: LLMs attend less to context in the middle of a
  long prompt. Order of retrieved docs matters.

## Evaluation

- **MRR (Mean Reciprocal Rank)**: how high in the list the first correct
  answer appears. `1/rank` averaged.
- **nDCG**: normalized Discounted Cumulative Gain. Position-sensitive
  ranked-retrieval metric. Standard for ranked search eval.
- **Faithfulness** (RAG): does the answer match the retrieved docs?
- **Context relevance** (RAG): are the retrieved docs actually about the
  query?
- **Answer relevance** (RAG): does the answer address the query?
- **Golden set**: hand-curated `(query, expected docs / answer)` pairs.
  Required for eval.
- **Cassettes**: deterministic recorded LLM/embedding responses, replayed
  in tests so eval and integration tests don't pay API cost.

## Production concerns

- **Embedding cache**: store text → vector to avoid re-embedding the same
  input.
- **Prompt caching**: provider-side cache of prompt prefixes for cost /
  latency.
- **Observability / tracing**: span every LLM and retrieval call, attribute
  cost and latency to stages. Tools: OpenTelemetry, Laminar, LangSmith.
- **Online vs offline eval**: offline uses cassettes / golden sets.
  Online compares prod variants (A/B), tracks regression.
