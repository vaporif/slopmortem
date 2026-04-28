.PHONY: install test smoke-live eval eval-record lint typecheck

# Redirect caches under the project so sandbox/Library restrictions don't bite.
export XDG_CACHE_HOME ?= $(CURDIR)/.uv-cache

install:
	uv sync

test:
	uv run pytest

smoke-live:
	RUN_LIVE=1 uv run pytest tests/smoke -v

# Default eval runs against cassettes via FakeLLMClient + FakeEmbeddingClient (no live API calls, deterministic).
eval:
	uv run python -m slopmortem.evals.runner --dataset tests/evals/datasets/seed.jsonl --baseline tests/evals/baseline.json

# Re-record cassettes against the live API. Costs real money; do not run in CI.
eval-record:
	RUN_LIVE=1 uv run python -m slopmortem.evals.runner --dataset tests/evals/datasets/seed.jsonl --baseline tests/evals/baseline.json --live --record

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run mypy slopmortem
