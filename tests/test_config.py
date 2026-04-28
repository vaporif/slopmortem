from __future__ import annotations

import pytest

from slopmortem.config import load_config


def test_defaults_load_from_toml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "slopmortem.toml").write_text("""\
K_retrieve = 30
N_synthesize = 5
ingest_concurrency = 20
facet_boost = 0.01
rrf_k = 60
slop_threshold = 0.7
max_doc_tokens = 50000
tier3_calibration_band = [0.65, 0.85]
max_cost_usd_per_query = 2.00
max_cost_usd_per_ingest = 15.00
openrouter_base_url = "https://openrouter.ai/api/v1"
model_facet = "anthropic/claude-haiku-4.5"
model_summarize = "anthropic/claude-haiku-4.5"
model_rerank = "anthropic/claude-sonnet-4.6"
model_synthesize = "anthropic/claude-sonnet-4.6"
""")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    c = load_config()
    assert c.K_retrieve == 30
    assert c.N_synthesize == 5
    assert c.K_retrieve >= c.N_synthesize
    assert c.openrouter_api_key.get_secret_value() == "sk-or-v1-test"


def test_typo_in_toml_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "slopmortem.toml").write_text("K_retreive = 30\n")  # typo
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    with pytest.raises(Exception):  # extra="forbid"
        load_config()


def test_K_retrieve_must_gte_N_synthesize(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "slopmortem.toml").write_text("K_retrieve = 3\nN_synthesize = 5\n")
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    with pytest.raises(Exception):
        load_config()
