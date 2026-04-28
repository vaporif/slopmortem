from __future__ import annotations

from typing import Any

import jsonref
from pydantic import BaseModel

from slopmortem.models import ToolSpec

__all__ = ["ToolSpec", "to_openai_input_schema", "to_strict_response_schema", "synthesis_tools"]


def to_openai_input_schema(args_model: type[BaseModel]) -> dict[str, Any]:
    schema = args_model.model_json_schema()
    inlined = jsonref.replace_refs(schema, proxies=False, lazy_load=False)
    if isinstance(inlined, dict):
        for k in ("$defs", "$schema", "$id"):
            inlined.pop(k, None)
    return dict(inlined)


def to_strict_response_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Emit a `response_format.json_schema.schema` payload that conforms to OpenAI
    strict-mode rules for models with Optional fields.

    Pydantic v2 omits any field with a default (incl. `T | None = None`) from the
    `required` list, but OpenAI strict mode mandates every property be `required` —
    nullability is expressed via `anyOf:[T,null]`, not by absence from `required`.
    This helper inlines `$ref`/`$defs`, strips draft metadata, and force-adds every
    top-level property (and every nested object's properties) to `required`. The
    `anyOf:[T,null]` shape is preserved verbatim. Idempotent: models with no
    Optional defaults round-trip unchanged.
    """
    schema = model.model_json_schema()
    inlined = jsonref.replace_refs(schema, proxies=False, lazy_load=False)
    if isinstance(inlined, dict):
        for k in ("$defs", "$schema", "$id"):
            inlined.pop(k, None)

    def _force_required(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "object" and "properties" in node:
            node["required"] = list(node["properties"].keys())
            node["additionalProperties"] = False
            for v in node["properties"].values():
                _force_required(v)
        for key in ("items", "anyOf", "oneOf", "allOf"):
            v = node.get(key)
            if isinstance(v, list):
                for elem in v:
                    _force_required(elem)
            elif isinstance(v, dict):
                _force_required(v)

    _force_required(inlined)
    return dict(inlined)


def synthesis_tools(config) -> list[ToolSpec]:
    """Factory — Tavily inclusion is config-driven and cannot be a constant."""
    from slopmortem.corpus.tools_impl import get_post_mortem, search_corpus
    tools = [get_post_mortem, search_corpus]
    if getattr(config, "enable_tavily_synthesis", False):
        from slopmortem.corpus.tools_impl import tavily_extract, tavily_search
        tools.extend([tavily_search, tavily_extract])
    return tools
