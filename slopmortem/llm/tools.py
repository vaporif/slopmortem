"""JSON-Schema helpers for tool definitions and OpenAI strict-mode response schemas."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import jsonref

from slopmortem.models import ToolSpec

if TYPE_CHECKING:
    from pydantic import BaseModel

    from slopmortem.config import Config

__all__ = ["ToolSpec", "synthesis_tools", "to_openai_input_schema", "to_strict_response_schema"]


def to_openai_input_schema(
    args_model: type[BaseModel],
) -> dict[str, Any]:  # type: ignore[explicit-any]  # JSON Schema is heterogeneous by spec
    """Render *args_model* as an OpenAI ``parameters`` schema with ``$ref`` inlined."""
    schema = args_model.model_json_schema()
    inlined = jsonref.replace_refs(schema, proxies=False, lazy_load=False)
    if isinstance(inlined, dict):
        for k in ("$defs", "$schema", "$id"):
            inlined.pop(k, None)
    return dict(inlined)


def _force_required(node: object) -> None:
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


def to_strict_response_schema(
    model: type[BaseModel],
) -> dict[str, Any]:  # type: ignore[explicit-any]  # JSON Schema is heterogeneous by spec
    """Emit a ``response_format.json_schema.schema`` payload for OpenAI strict mode.

    Pydantic v2 omits any field with a default (incl. ``T | None = None``) from the
    ``required`` list, but OpenAI strict mode mandates every property be ``required`` —
    nullability is expressed via ``anyOf:[T,null]``, not by absence from ``required``.
    This helper inlines ``$ref``/``$defs``, strips draft metadata, and force-adds every
    top-level property (and every nested object's properties) to ``required``. The
    ``anyOf:[T,null]`` shape is preserved verbatim. Idempotent: models with no
    Optional defaults round-trip unchanged.
    """
    schema = model.model_json_schema()
    inlined = jsonref.replace_refs(schema, proxies=False, lazy_load=False)
    if isinstance(inlined, dict):
        for k in ("$defs", "$schema", "$id"):
            inlined.pop(k, None)
    _force_required(inlined)
    return dict(inlined)


def synthesis_tools(config: Config) -> list[ToolSpec]:
    """Factory — Tavily inclusion is config-driven and cannot be a constant."""
    # Lazy import to break the cycle with corpus.tools_impl, which imports ToolSpec from models
    # via this module's transitive deps.
    from slopmortem.corpus.tools_impl import (  # noqa: PLC0415 — break import cycle
        get_post_mortem,
        search_corpus,
        tavily_extract,
        tavily_search,
    )

    tools = [get_post_mortem, search_corpus]
    if getattr(config, "enable_tavily_synthesis", False):
        tools.extend([tavily_search, tavily_extract])
    return tools
