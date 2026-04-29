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
    """All knobs slopmortem reads at startup — TOML overrides env, env overrides defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="forbid",
        toml_file=("slopmortem.toml", "slopmortem.local.toml"),
    )

    K_retrieve: int = 30
    N_synthesize: int = 5
    ingest_concurrency: int = 20
    facet_boost: float = 0.01
    rrf_k: int = 60
    slop_threshold: float = 0.7
    max_doc_tokens: int = 50000
    tier3_calibration_band: tuple[float, float] = (0.65, 0.85)
    max_cost_usd_per_query: float = 2.00
    max_cost_usd_per_ingest: float = 15.00

    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    model_facet: str = "anthropic/claude-haiku-4.5"
    model_summarize: str = "anthropic/claude-haiku-4.5"
    model_rerank: str = "anthropic/claude-sonnet-4.6"
    model_synthesize: str = "anthropic/claude-sonnet-4.6"

    embedding_provider: str = "fastembed"
    embed_model_id: str = "nomic-ai/nomic-embed-text-v1.5"
    embed_cache_dir: Path | None = None
    retry_max_attempts: int = 3
    retry_initial_backoff: float = 1.0

    taxonomy_version: str = "v1"
    reliability_rank_version: str = "v1"

    enable_tavily_enrich: bool = False
    enable_tavily_synthesis: bool = False
    enable_wayback: bool = False
    enable_crunchbase: bool = False
    enable_tracing: bool = False
    strict_deaths: bool = False

    tavily_calls_per_synthesis: int = 2  # spec line 1005

    openrouter_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    tavily_api_key: SecretStr = SecretStr("")
    laminar_api_key: SecretStr = SecretStr("")

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
