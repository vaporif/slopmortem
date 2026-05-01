# Cassettes

Committed JSON files that replay LLM, embedding, and BM25 calls so `just eval` runs offline, with no API keys.

## Replay

`just eval` runs the canonical eval against the committed cassettes and an ephemeral Qdrant collection seeded from `tests/fixtures/corpus_fixture.jsonl`. The only runtime prerequisite is Qdrant on `localhost:6333`.

```bash
just eval
```

Under the hood: `FakeLLMClient` + `FakeEmbeddingClient` read from `tests/fixtures/cassettes/evals/<row_id>/`, the corpus fixture seeds a throwaway Qdrant collection, and the collection is dropped on exit.

## Recording the canonical eval

Recording re-issues every call against the live API. It costs real money and is capped by `--max-cost-usd`.

```bash
just eval-record
```

Recording needs a populated `tests/fixtures/corpus_fixture.jsonl`. Regenerate that fixture (rarely) with:

```bash
just eval-record-corpus
```

One row at a time:

```bash
uv run python -m slopmortem.evals.runner --record --scope <row_id> --max-cost-usd 2.0
```

## Recording for a custom test

For a one-off scope outside the canonical eval, call `record_cassettes_for_inputs(...)` directly. Output goes under `tests/fixtures/cassettes/custom/`, which is reserved for ad-hoc sets.

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

The helper writes to a temp directory and atomically swaps it in, so a SIGKILL mid-record won't leave a half-populated cassette dir. Tavily is forced off during recording to keep things deterministic.

## Schema

Pydantic envelopes are in `slopmortem/evals/cassettes.py`. Writers and readers share that import so record and replay can't disagree.

Per-row layout under `tests/fixtures/cassettes/evals/<row_id>/`:

- `<stage>__<model>__<sha>.json` — LLM call
- `embed__<sha>.json` — embedding
- `sparse__<sha>.json` — BM25 sparse

Schema version is `<major>.<minor>`. Readers hard-fail on a major mismatch and tolerate any minor at the same major (additive fields only).

## Troubleshooting

- `NoCannedResponseError` — the LLM cassette is missing. Prompt or model params changed since the last recording. Re-record with `--record --scope <row_id>`.
- `NoCannedEmbeddingError` — the embedding cassette is missing, usually because the upstream text changed. Re-record the affected scope.
- `corpus_fixture_sha256` mismatch — the fixture drifted from the sha pinned in the cassette envelope. Either revert the fixture or regenerate it with `just eval-record-corpus` and re-record the dependents.
- LFS pointer in place of binary content — `git lfs install` was skipped after clone. Run it once per machine, then `git lfs pull`.

## CI / onboarding

Once per dev machine:

```bash
git lfs install
```

In CI, use `actions/checkout@v4` with `lfs: true` so cassettes and the corpus fixture come down as real content, not LFS pointers:

```yaml
- uses: actions/checkout@v4
  with:
    lfs: true
```

`nix develop` users get `git-lfs` from the dev shell already.
