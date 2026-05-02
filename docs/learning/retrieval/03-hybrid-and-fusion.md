# Hybrid retrieval and rank fusion

Why production search systems combine dense and sparse retrieval, and how
fusion actually works.

## The two failure modes

Dense (semantic) and sparse (lexical) retrieval fail in opposite ways. A
short tour:

### Dense fails on

- **Rare proper nouns.** "Magic Leap" embeds near "AR/VR startup" generically
  and the embedder may not preserve its uniqueness.
- **Out-of-distribution jargon.** A term the embedding model never saw
  during training (a new framework, a niche regulatory term) is mapped to
  nearby clusters by guesswork.
- **Short queries dominated by one term.** "Rust ORM" pulls in "Rust web
  framework" or "Python ORM" because those are conceptually adjacent.
- **Negation.** "without authentication" embeds close to "with
  authentication" — the embedding lives in a shared topic cluster.

### Sparse (BM25) fails on

- **Paraphrase.** "shared workspace" vs "coworking" — zero token overlap, so
  BM25 returns nothing useful.
- **Cross-language.** No token overlap between languages.
- **Conceptual analogies.** "Uber but for X" against a corpus that calls it
  "on-demand Y marketplace" misses on terms.
- **Implicit context.** Queries that lean on knowledge the doc doesn't
  spell out word-for-word.

These failure modes barely overlap, so running both and merging recovers
most of what either alone misses.

## Why you can't just add scores

BM25 might output 4.2 for the top hit. Cosine similarity outputs 0.87. They
are not on the same scale; they aren't even the same kind of quantity (BM25
is unbounded, cosine is in `[-1, 1]`). Summing them is meaningless and
dominated by whichever side has bigger numbers.

You can try to normalize — min-max scale each list to `[0, 1]`, then sum.
This kind of works but is brittle:

- The min and max change query-to-query; calibration drifts.
- Outliers (one very high BM25 score on a single hit) compress everything
  else.
- Weights between dense and sparse become a tunable that has to be
  re-tuned per dataset.

Rank-based fusion sidesteps all of this.

## Reciprocal Rank Fusion (RRF)

The simplest robust fusion. For each item across all input lists:

```
RRF_score(item) = Σ over each list  1 / (k + rank_in_list(item))
```

`k` is a smoothing constant, typically 60 (from the original 2009 paper by
Cormack et al.). If an item doesn't appear in a list, that list contributes
zero.

Properties:

- Ignores raw scores entirely. Only ranks matter.
- Items that rank high in *both* lists score highest — the "agreement"
  signal you want.
- Items that appear in only one list still score, weighted by where they
  ranked.
- No tunable weights between systems by default. (You can add weights,
  but the unweighted version is hard to beat without per-dataset tuning.)
- Numerically stable, fast, trivially parallel.

That's why RRF is the default fusion in Qdrant, Vespa, Elasticsearch's
hybrid mode, and most academic baselines.

## Reranking on top of fusion

Hybrid retrieval gets you a high-recall list (say, top 50). The top of that
list is mostly right, but order is noisy. A reranker rescores the small
final list with a stronger model.

Two common rerankers:

- **Cross-encoder** (e.g. `bge-reranker`, `cohere-rerank`): a transformer
  that reads the query and a candidate doc *together* and outputs one
  score. Much more accurate than bi-encoder cosine because the query and
  doc attend to each other directly. Too expensive to run on millions of
  docs; perfect for top 50.
- **LLM-as-reranker**: prompt an LLM with the query and each candidate,
  ask for a relevance score or a sorted list. Slowest, most flexible.
  Useful when you need reasoning over the doc, not just topical match.

The full pipeline that wins most public benchmarks:

```
query → dense retriever (top 100) ┐
        sparse retriever (top 100) ┘ → RRF → top 50 → cross-encoder rerank → top 10
```

That's roughly the modern default for any production search or RAG system.

## When you can skip hybrid

There are corpora where dense alone is fine and sparse adds little:

- Short, conversational text where vocabulary is naturally varied.
- Cases where the query and corpus are both written by the same author
  population (consistent terminology).
- Pure semantic-similarity tasks (find me docs *about* X), not lookup
  tasks (find me the doc that mentions X).

And cases where sparse alone is fine:

- Code search where exact identifiers matter.
- Compliance / legal lookup where specific clauses must match
  word-for-word.
- Logs and structured data with stable terminology.

The default for general document corpora is hybrid + RRF + rerank. Skip
parts of it deliberately, not by accident.

## Evaluation matters more than model choice

You can't tell whether hybrid helps your corpus without measuring. The bare
minimum:

1. A hand-curated set of `(query, ideal-doc-ids)` pairs. 50 is a useful
   start; 200 is enough to be confident.
2. Run each variant (dense-only, sparse-only, hybrid, hybrid+rerank).
3. Score each variant on `nDCG@10` or `recall@10`.

Without this, "we use hybrid because everyone does" is a vibe, not a
decision. With it, you'll quickly see which parts of the pipeline pay for
themselves.

## Further reading

- Cormack, Clarke, Buettcher (2009), *Reciprocal Rank Fusion outperforms
  Condorcet and individual Rank Learning Methods*. Five pages, the source.
- Pinecone Learning Center: posts on hybrid search and reranking.
- Qdrant docs: "Hybrid Queries" and "Distance and similarity."
- Vespa.ai blog: posts on multi-phase ranking. Excellent on production
  trade-offs.
- The BEIR benchmark paper (Thakur et al., 2021) for cross-task retrieval
  evaluation.
