# Cassettes

Cassettes are committed JSON files that replay LLM, embedding, and BM25 calls
deterministically so `just eval` can run without network or API keys. This
guide covers how to record, replay, and troubleshoot them.

## Quick start (replay)

`just eval` runs the canonical eval against committed cassettes plus an
ephemeral Qdrant collection seeded from `tests/fixtures/corpus_fixture.jsonl`.
No API keys are required. The only runtime prerequisite is a Qdrant instance
reachable on `localhost:6333`.

```bash
just eval
```

Under the hood the runner wires `FakeLLMClient` + `FakeEmbeddingClient`
against `tests/fixtures/cassettes/evals/<row_id>/` and seeds a throwaway
Qdrant collection from the corpus fixture. The collection is dropped on exit.

## Recording for the canonical eval

Recording re-issues every cassette against the live API. It costs real money
on the LLM side and is capped by `--max-cost-usd`.

```bash
just eval-record
```

Recording requires a populated `tests/fixtures/corpus_fixture.jsonl`. The
operator regenerates that fixture (rarely) via:

```bash
just eval-record-corpus
```

To re-record a single row only:

```bash
uv run python -m slopmortem.evals.runner --record --scope <row_id> --max-cost-usd 2.0
```

## Recording for a custom test (Layer 2 walkthrough)

Test authors who need cassettes for a one-off scope outside the canonical
eval call `record_cassettes_for_inputs(...)` directly. Output goes under
`tests/fixtures/cassettes/custom/` (the subtree is reserved for ad-hoc sets).

```python
import asyncio
from pathlib import Path

from slopmortem.config import load_config
from slopmortem.evals.recording_helper import record_cassettes_for_inputs
from slopmortem.models import InputContext

asyncio.run(record_cassettes_for_inputs(
    inputs=[InputContext(name="my-scope", description="...")],
    output_dir=Path("tests/fixtures/cassettes/custom"),
    corpus_fixture_path=Path("tests/fixtures/corpus_fixture.jsonl"),
    config=load_config(),
))
```

The helper writes into a temp directory and atomically swaps it into place,
so a SIGKILL mid-record will not leave a half-populated cassette directory.
Tavily is forced off during recording to keep cassettes deterministic.

## Cassette schema reference

The Pydantic envelope models live in `slopmortem/evals/cassettes.py`. That
module is the single source of truth for both writers and readers; record
and replay can never disagree on disk shape.

Per-row directory layout under `tests/fixtures/cassettes/evals/<row_id>/`:

- `<stage>__<model>__<sha>.json` — LLM call cassette
- `embed__<sha>.json` — embedding cassette
- `sparse__<sha>.json` — BM25 sparse cassette

Schema version is `<major>.<minor>`. Readers hard-fail on a major mismatch
and tolerate any minor at the same major (additive fields only).

## Troubleshooting

- `NoCannedResponseError` — the LLM cassette for the requested key is
  missing. The upstream prompt or model parameters changed since the last
  recording. Re-record with `--record --scope <row_id>`.
- `NoCannedEmbeddingError` — the embedding cassette is missing, usually
  because the upstream text changed. Re-record the affected scope.
- `corpus_fixture_sha256` mismatch — the corpus fixture drifted from the
  sha pinned in the cassette envelope. Either revert the fixture to the
  committed sha or regenerate it with `just eval-record-corpus` and
  re-record the dependent cassettes.
- LFS pointer file showing in place of binary content — `git lfs install`
  was skipped after clone. Run `git lfs install` once per machine, then
  `git lfs pull` to fetch the real content.

## CI/onboarding

One-time per developer machine:

```bash
git lfs install
```

In CI, use `actions/checkout@v4` with `lfs: true` so the cassettes and
corpus fixture are materialized rather than left as LFS pointers:

```yaml
- uses: actions/checkout@v4
  with:
    lfs: true
```

The dev shell bundles `git-lfs` (added to `flake.nix` in Task 4), so
`nix develop` users get it without extra setup.
