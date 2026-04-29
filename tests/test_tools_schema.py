from __future__ import annotations

import jsonschema
from pydantic import BaseModel

from slopmortem.llm.tools import to_openai_input_schema, to_strict_response_schema


class Args(BaseModel):
    q: str
    limit: int | None = None
    facets: list[str] = []


def test_inlines_refs_and_strips_metadata():
    schema = to_openai_input_schema(Args)
    # No $defs / $ref / $schema — Anthropic-via-OpenRouter rejects these
    assert "$defs" not in schema
    assert "$schema" not in schema
    assert "$id" not in schema
    assert "$ref" not in str(schema)
    # Optional kept as anyOf:[T,null] (Pydantic default emission)
    limit = schema["properties"]["limit"]
    assert limit.get("anyOf") == [{"type": "integer"}, {"type": "null"}]


def test_round_trip_pydantic_to_schema_to_pydantic():
    schema = to_openai_input_schema(Args)
    sample = {"q": "scrap metal marketplace", "limit": 5, "facets": ["sector"]}
    parsed = Args.model_validate(sample)
    assert parsed.q == sample["q"]
    # schema accepts the sample
    jsonschema.validate(sample, schema)


def test_to_strict_response_schema_force_requires_optional_defaults():
    # OpenAI strict mode: every property must be in `required`. Pydantic omits
    # fields with a default (incl. `T | None = None`) — the helper adds them back.
    schema = to_strict_response_schema(Args)
    assert set(schema["required"]) == {"q", "limit", "facets"}
    assert schema["properties"]["limit"].get("anyOf") == [{"type": "integer"}, {"type": "null"}]
    assert schema["additionalProperties"] is False


def test_to_strict_response_schema_idempotent_when_no_optional_defaults():
    class AllRequired(BaseModel):
        a: str
        b: int | None  # required (no default), nullable

    schema = to_strict_response_schema(AllRequired)
    assert set(schema["required"]) == {"a", "b"}
