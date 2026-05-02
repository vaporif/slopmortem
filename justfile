set quiet := true

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

# Fast import-time + cassette smoke. Catches typer registration regressions
# and LLM-pipeline drift without hitting any live API. Used by every
# refactor checkpoint in docs/plans/2026-05-02-encapsulation-refactor.md.
smoke:
    uv run slopmortem --help
    uv run slopmortem ingest --help
    uv run slopmortem query --help
    uv run slopmortem replay --help
    uv run slopmortem embed-prefetch --help
    just eval

lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run lint-imports

format:
    uv run ruff check --fix .
    uv run ruff format .

typecheck:
    uv run basedpyright

# Ingest the first N entries with all enrichers + the Crunchbase CSV adapter.
# Default N=50. Override: `just ingest 100`, `just ingest 500`. Use `just ingest-all` for no limit.
ingest LIMIT="50":
    uv run slopmortem ingest \
        --limit {{LIMIT}} \
        --enrich-wayback \
        --tavily-enrich \
        --crunchbase-csv data/crunchbase/companies-closed.csv

# Ingest the entire corpus (no --limit). Same enrichers as `ingest`. Cost scales with corpus size.
ingest-all:
    uv run slopmortem ingest \
        --enrich-wayback \
        --tavily-enrich \
        --crunchbase-csv data/crunchbase/companies-closed.csv

# Run the full query pipeline (facet -> retrieve -> rerank -> synthesize) against PITCH.
# Append extra flags after the pitch, e.g.: `just query "..." --name MyCo --years 10`.
query PITCH *FLAGS:
    uv run slopmortem query {{quote(PITCH)}} {{FLAGS}}

# Run retrieve-only against PITCH; skips rerank + synthesize. Cheap, useful for tuning facets/retrieval.
query-debug PITCH *FLAGS:
    uv run slopmortem query {{quote(PITCH)}} --debug-retrieve {{FLAGS}}

# Run once on a fresh checkout, before `just ingest`. The recipe is idempotent
# and re-runnable: existing values are shown masked (first 4 + last 4 chars)
# and pressing Enter keeps them; typing a new value overwrites. Blank inputs
# for never-set keys are skipped so optional keys stay out of the file until
# you need them.
#
# Keys prompted (in order):
#   OPENROUTER_API_KEY   required — every LLM call routes through OpenRouter
#   TAVILY_API_KEY       optional — only for `--tavily-enrich` ingest +
#                                   Tavily-augmented synthesis
#   OPENAI_API_KEY       optional — only if you flip `embedding_provider` from
#                                   the default fastembed to openai in
#                                   slopmortem.toml
#   LMNR_PROJECT_API_KEY optional — only if `enable_tracing = true` in
#                                   slopmortem.toml (Laminar tracing)
# Interactively populate .env with the API keys slopmortem reads at startup.
init-env:
    #!/usr/bin/env bash
    set -euo pipefail
    ENV_FILE=.env
    touch "$ENV_FILE"

    upsert() {
        local key="$1" desc="$2" required="$3"
        local current label prompt value
        current=$(grep -E "^${key}=" "$ENV_FILE" | sed -E "s/^${key}=//" | head -1 || true)
        if [ -n "$current" ] && [ "${#current}" -gt 8 ]; then
            label="[${current:0:4}…${current: -4}]"
        elif [ -n "$current" ]; then
            label="[set]"
        elif [ "$required" = "yes" ]; then
            label="(required)"
        else
            label="(optional, leave blank to skip)"
        fi
        prompt="${key} — ${desc} ${label}: "
        read -r -p "$prompt" value
        if [ -z "$value" ]; then
            return 0
        fi
        if grep -qE "^${key}=" "$ENV_FILE"; then
            sed -i.bak -E "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
            rm -f "$ENV_FILE.bak"
        else
            printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
        fi
    }

    upsert OPENROUTER_API_KEY   "every LLM call routes through OpenRouter"                       yes
    upsert TAVILY_API_KEY       "needed for --tavily-enrich during ingest and Tavily synthesis"  no
    upsert OPENAI_API_KEY       "only if you switch embedding_provider from fastembed to openai" no
    upsert LMNR_PROJECT_API_KEY "Laminar tracing; only if enable_tracing=true in slopmortem.toml" no

    echo "wrote $ENV_FILE"

# Wipe all ingested state: stop Qdrant, delete its storage volume, drop
# the merge journal, and remove the post_mortems tree. Prompts before
# touching anything. Run before a fresh `just ingest` when you want to
# start from zero.
nuke:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "This will delete:"
    echo "  - data/qdrant/         (Qdrant storage volume)"
    echo "  - journal.sqlite       (merge journal)"
    echo "  - post_mortems/        (raw + canonical + quarantine docs)"
    read -r -p "Proceed? [y/N] " confirm
    case "$confirm" in
        y|Y|yes|YES) ;;
        *) echo "aborted"; exit 2 ;;
    esac
    docker compose down qdrant 2>/dev/null || true
    rm -rf data/qdrant journal.sqlite post_mortems
    echo "nuked. run 'docker compose up -d qdrant' + 'just ingest' to rebuild."
