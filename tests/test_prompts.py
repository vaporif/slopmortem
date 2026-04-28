from __future__ import annotations

import json
from pathlib import Path

from slopmortem.llm.prompts import prompt_template_sha, render_blocks, render_prompt
from slopmortem.models import Facets, LlmRerankResult, Synthesis

FIXTURES = Path(__file__).parent / "fixtures" / "prompts"


def test_facet_extract_renders():
    out = render_prompt("facet_extract", description="we sell scrap metal")
    assert "scrap metal" in out
    assert "<untrusted_document" in out
    assert "fintech" in out


def test_facet_extract_paired_schema_loads():
    schema = Facets.model_json_schema()
    assert "sector" in schema["properties"]


def test_llm_rerank_renders_with_candidates():
    out = render_prompt(
        "llm_rerank",
        pitch="medical scribing for dermatologists",
        facets={"sector": "healthtech"},
        candidates=[
            {"candidate_id": "a.com", "summary": "ehr integration vendor", "name": "A"},
        ],
    )
    assert "a.com" in out
    schema = LlmRerankResult.model_json_schema()
    assert "ranked" in schema["properties"]


def test_synthesize_renders_inlined_body():
    out = render_prompt(
        "synthesize",
        pitch="x",
        candidate_id="a.com",
        candidate_name="A",
        candidate_body="<full markdown>",
    )
    assert "<untrusted_document" in out and "</untrusted_document>" in out
    assert "<full markdown>" in out
    schema = Synthesis.model_json_schema()
    assert "where_diverged" in schema["properties"]


def test_summarize_renders_with_source_id():
    out = render_prompt(
        "summarize",
        source_id="hn:42",
        body="acme failed because regulators",
    )
    assert "acme failed" in out
    assert 'source="hn:42"' in out


def test_render_blocks_splits_system_and_user():
    blocks = render_blocks("facet_extract", description="we sell scrap metal")
    assert set(blocks.keys()) == {"system", "user"}
    assert "fintech" in blocks["system"]
    assert "scrap metal" in blocks["user"]
    assert "fintech" not in blocks["user"]


def test_prompt_template_sha_is_deterministic():
    a = prompt_template_sha("facet_extract")
    b = prompt_template_sha("facet_extract")
    assert a == b
    assert len(a) == 16
