from __future__ import annotations

import os
from pathlib import Path

import pytest
from openai import AsyncOpenAI

from slopmortem.budget import Budget
from slopmortem.llm import OpenRouterClient

CASSETTE_FILE = (
    Path(__file__).parent
    / "cassettes"
    / "test_openrouter_cassette"
    / "test_facet_extract_round_trip.yaml"
)


@pytest.mark.vcr
async def test_facet_extract_round_trip():
    if not CASSETTE_FILE.exists() and not os.environ.get("RECORD"):
        pytest.skip(
            f"no cassette at {CASSETTE_FILE}; rerun with RECORD=1 + OPENROUTER_API_KEY to record"
        )
    api_key = os.environ.get("OPENROUTER_API_KEY", "sk-or-v1-test")
    sdk = AsyncOpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
    c = OpenRouterClient(sdk=sdk, budget=Budget(2.0), model="anthropic/claude-haiku-4.5")
    r = await c.complete("Extract facets from: marketplace for industrial scrap metal.")
    assert len(r.text) > 0
    assert r.cost_usd >= 0
