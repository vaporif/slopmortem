# Redirect caches under the project so sandbox/Library restrictions don't bite.
export XDG_CACHE_HOME := env_var_or_default("XDG_CACHE_HOME", justfile_directory() / ".uv-cache")

default:
    @just --list

install:
    uv sync

# Filter the bundled Crunchbase 2015 export to closed-only companies.
# Output: data/crunchbase/companies-closed.csv (~6.2K rows, tracked in repo).
crunchbase:
    uv run python scripts/filter_crunchbase_closed.py

test:
    uv run pytest -n auto

coverage:
    uv run pytest -n auto --cov=slopmortem --cov-report=term-missing --cov-report=html

smoke-live:
    RUN_LIVE=1 uv run pytest tests/smoke -v

# Default eval runs against cassettes via FakeLLMClient + FakeEmbeddingClient (no live API calls, deterministic).
eval:
    uv run python -m slopmortem.evals.runner --dataset tests/evals/datasets/seed.jsonl --baseline tests/evals/baseline.json

# Re-record cassettes against live OpenRouter + local fastembed; LLM-side cost only.
# Run sparingly. Default cost ceiling --max-cost-usd=2.0 in the runner.
eval-record:
    RUN_LIVE=1 uv run python -m slopmortem.evals.runner \
        --dataset tests/evals/datasets/seed.jsonl \
        --baseline tests/evals/baseline.json \
        --record \
        --max-cost-usd 2.0

# Regenerate the seed corpus fixture from corpus_fixture_inputs.yml. Run rarely.
# Cost: ~$0.30-$1 under the default fastembed embedding provider.
eval-record-corpus:
    RUN_LIVE=1 uv run python -m slopmortem.evals.corpus_recorder \
        --inputs tests/fixtures/corpus_fixture_inputs.yml \
        --out tests/fixtures/corpus_fixture.jsonl

lint:
    uv run ruff check .
    uv run ruff format --check .

format:
    uv run ruff check --fix .
    uv run ruff format .

typecheck:
    uv run basedpyright
