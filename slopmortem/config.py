"""Config loader: TOML, env, and secrets, validated on load."""

from __future__ import annotations

from pathlib import Path
from typing import override

from pydantic import SecretStr, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


class Config(BaseSettings):
    """All knobs slopmortem reads at startup. TOML overrides env, env overrides defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="forbid",
        toml_file=("slopmortem.toml", "slopmortem.local.toml"),
    )

    K_retrieve: int = 30
    N_synthesize: int = 5
    min_similarity_score: float = 4.0
    ingest_concurrency: int = 20
    facet_boost: float = 0.01
    rrf_k: int = 60
    slop_threshold: float = 0.7
    max_doc_tokens: int = 8000
    tier3_calibration_band: tuple[float, float] = (0.65, 0.85)
    max_cost_usd_per_query: float = 2.00
    max_cost_usd_per_ingest: float = 15.00

    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    model_facet: str = "anthropic/claude-haiku-4.5"
    model_summarize: str = "anthropic/claude-haiku-4.5"
    model_rerank: str = "anthropic/claude-sonnet-4.6"
    model_synthesize: str = "anthropic/claude-sonnet-4.6"

    # Per-stage output caps. OpenRouter requires holding upfront credit for the
    # model's max output, so leaving these unset reserves the full 64K Anthropic
    # ceiling and surfaces as HTTP 402 on low-balance keys. Values are sized to
    # each stage's actual output shape with slack.
    max_tokens_facet: int = 2000
    max_tokens_summarize: int = 1500
    max_tokens_rerank: int = 4000
    max_tokens_synthesize: int = 16000
    max_tokens_slop_judge: int = 64
    max_tokens_tiebreaker: int = 256

    embedding_provider: str = "fastembed"
    embed_model_id: str = "nomic-ai/nomic-embed-text-v1.5"
    embed_cache_dir: Path | None = None
    retry_max_attempts: int = 3
    retry_initial_backoff: float = 1.0

    taxonomy_version: str = "v1"
    reliability_rank_version: str = "v1"

    enable_tavily_synthesis: bool = False
    enable_tracing: bool = False
    strict_deaths: bool = False

    tavily_calls_per_synthesis: int = 2  # spec line 1005

    openrouter_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    tavily_api_key: SecretStr = SecretStr("")
    laminar_api_key: SecretStr = SecretStr("")

    # Infrastructure knobs populated from env (``LMNR_*``, ``QDRANT_*``,
    # ``POST_MORTEMS_ROOT``, ``MERGE_JOURNAL_PATH``). Declared here so the
    # CLI can read them off ``Config`` instead of ``os.environ`` and so
    # ``extra="forbid"`` doesn't reject ``.env`` entries for them.
    lmnr_project_api_key: SecretStr = SecretStr("")
    lmnr_base_url: str = ""
    lmnr_allow_remote: str = ""
    qdrant_host: str = "localhost"
    # Non-standard 16333 so the project's docker-compose Qdrant doesn't collide
    # with a pre-existing 6333 instance on the dev box. The container internally
    # still serves 6333; only the host-side publish port is bumped.
    qdrant_port: int = 16333
    qdrant_collection: str = "slopmortem"
    post_mortems_root: str = "./post_mortems"
    merge_journal_path: str = ""

    @model_validator(mode="after")
    def _check_k_ge_n(self) -> Config:
        if self.K_retrieve < self.N_synthesize:
            msg = f"K_retrieve ({self.K_retrieve}) must be >= N_synthesize ({self.N_synthesize})"
            raise ValueError(msg)
        return self

    @classmethod
    @override
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Wire TOML sources after env and before secrets so TOML wins over env at runtime."""
        toml_files: list[Path] = [
            p
            for name in ("slopmortem.toml", "slopmortem.local.toml")
            if (p := Path.cwd() / name).exists()
        ]
        toml = TomlConfigSettingsSource(settings_cls, toml_file=toml_files or None)
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            toml,
            file_secret_settings,
        )


def load_config() -> Config:
    """Build a ``Config`` from the current TOML + env + dotenv state."""
    return Config()
