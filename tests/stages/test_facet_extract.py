"""Tests for the ``facet_extract`` stage: taxonomy-valid output and ``other`` fallback."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml

from conftest import llm_canned_key
from slopmortem.llm.fake import FakeLLMClient, FakeResponse
from slopmortem.llm.prompts import render_prompt
from slopmortem.stages.facet_extract import extract_facets

if TYPE_CHECKING:
    from collections.abc import Mapping

_TAXONOMY_PATH = Path(__file__).resolve().parents[2] / "slopmortem" / "corpus" / "taxonomy.yml"


@pytest.fixture
def taxonomy() -> Mapping[str, list[str]]:
    return yaml.safe_load(_TAXONOMY_PATH.read_text())


_DEFAULT_MODEL = "test-model"


def _canned(text: str, *, description: str) -> dict[tuple[str, str, str], FakeResponse]:
    # ``extract_facets`` is called without an explicit model, so ``FakeLLMClient``
    # keys lookups on its ``default_model``.
    rendered = render_prompt("facet_extract", description=description)
    return {
        llm_canned_key("facet_extract", model=_DEFAULT_MODEL, prompt=rendered): FakeResponse(
            text=text
        ),
    }


async def test_facet_extract_returns_taxonomy_valid_facets(
    taxonomy: Mapping[str, list[str]],
) -> None:
    payload = json.dumps(
        {
            "sector": "retail_ecommerce",
            "business_model": "b2b_marketplace",
            "customer_type": "smb",
            "geography": "us",
            "monetization": "transaction_fee",
            "sub_sector": "industrial scrap metal",
            "product_type": "marketplace listing",
            "price_point": "tiered",
            "founding_year": 2018,
            "failure_year": 2021,
        }
    )
    description = "marketplace for industrial scrap metal"
    fake_llm = FakeLLMClient(
        canned=_canned(payload, description=description), default_model=_DEFAULT_MODEL
    )

    facets = await extract_facets(description, fake_llm)

    assert facets.sector in taxonomy["sector"]
    assert facets.business_model in taxonomy["business_model"]
    assert facets.customer_type in taxonomy["customer_type"]
    assert facets.geography in taxonomy["geography"]
    assert facets.monetization in taxonomy["monetization"]


async def test_facet_extract_uses_other_when_unclear() -> None:
    payload = json.dumps(
        {
            "sector": "other",
            "business_model": "other",
            "customer_type": "other",
            "geography": "other",
            "monetization": "other",
        }
    )
    description = "we sell things"
    fake_llm = FakeLLMClient(
        canned=_canned(payload, description=description), default_model=_DEFAULT_MODEL
    )

    facets = await extract_facets(description, fake_llm)

    assert facets.sector == "other"
    assert facets.business_model == "other"
