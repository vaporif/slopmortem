# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false
"""JSON-Schema helpers for tool definitions and OpenAI strict-mode response schemas.

`jsonref` ships no stubs, so calls into it surface as `Unknown`. We assert
the shape we know we produced (a dict from `model_json_schema()`) and
silence the `reportUnknown*` family at the file boundary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import jsonref

from slopmortem.models import ToolSpec

if TYPE_CHECKING:
    from pydantic import BaseModel

    from slopmortem.config import Config

__all__ = ["ToolSpec", "synthesis_tools", "to_openai_input_schema", "to_strict_response_schema"]


def to_openai_input_schema(
    args_model: type[BaseModel],
) -> dict[str, Any]:  # pyright: ignore[reportExplicitAny]
    """Render *args_model* as an OpenAI ``parameters`` schema with ``$ref`` inlined."""
    schema = args_model.model_json_schema()
    inlined = jsonref.replace_refs(schema, proxies=False, lazy_load=False)
    if not isinstance(inlined, dict):
        msg = f"expected dict from jsonref.replace_refs, got {type(inlined).__name__}"
        raise TypeError(msg)
    for k in ("$defs", "$schema", "$id"):
        inlined.pop(k, None)
    return cast("dict[str, Any]", dict(inlined))  # pyright: ignore[reportExplicitAny]


def _force_required(node: object) -> None:
    if not isinstance(node, dict):
        return
    if node.get("type") == "object" and "properties" in node:
        node["required"] = list(node["properties"])
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
) -> dict[str, Any]:  # pyright: ignore[reportExplicitAny]
    """Emit a ``response_format.json_schema.schema`` payload for OpenAI strict mode.

    Pydantic v2 omits fields with defaults (including ``T | None = None``)
    from ``required``, but OpenAI strict mode wants every property in
    ``required``; nullability is expressed via ``anyOf:[T,null]`` instead.
    This helper inlines ``$ref``/``$defs``, strips draft metadata, and adds
    every top-level property (and every nested object's properties) to
    ``required``. The ``anyOf:[T,null]`` shape is preserved as-is.
    Idempotent: models without Optional defaults round-trip unchanged.
    """
    schema = model.model_json_schema()
    inlined = jsonref.replace_refs(schema, proxies=False, lazy_load=False)
    if not isinstance(inlined, dict):
        msg = f"expected dict from jsonref.replace_refs, got {type(inlined).__name__}"
        raise TypeError(msg)
    for k in ("$defs", "$schema", "$id"):
        inlined.pop(k, None)
    _force_required(inlined)
    return cast("dict[str, Any]", dict(inlined))  # pyright: ignore[reportExplicitAny]


def synthesis_tools(config: Config) -> list[ToolSpec]:
    """Build the synthesis tool list. Tavily inclusion depends on config, so it isn't a constant."""
    # Lazy import to break the cycle with corpus.tools_impl, which imports
    # ToolSpec from models through this module's transitive deps.
    from slopmortem.corpus import tools_impl  # noqa: PLC0415 — break import cycle
    from slopmortem.corpus.tools_impl import (  # noqa: PLC0415 — break import cycle
        get_post_mortem,
        search_corpus,
        tavily_extract,
        tavily_search,
    )

    tools = [get_post_mortem, search_corpus]
    if config.enable_tavily_synthesis:
        # Each synthesize() call gets its own quota (spec line 1005:
        # <=2 Tavily calls per synthesis), shared across both tools.
        used = 0
        cap = config.tavily_calls_per_synthesis

        async def _bounded_search(*, q: str, limit: int = 5) -> str:
            nonlocal used
            if used >= cap:
                return f"tavily call budget exceeded ({cap} per synthesis); refusing"
            used += 1
            # Runtime attr lookup so tests can monkeypatch the impl.
            return await tools_impl._tavily_search(q, limit)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]

        async def _bounded_extract(*, url: str) -> str:
            nonlocal used
            if used >= cap:
                return f"tavily call budget exceeded ({cap} per synthesis); refusing"
            used += 1
            # Runtime attr lookup so tests can monkeypatch the impl.
            return await tools_impl._tavily_extract(url)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]

        tools.extend(
            [
                ToolSpec(
                    name=tavily_search.name,
                    description=tavily_search.description,
                    args_model=tavily_search.args_model,
                    fn=_bounded_search,
                ),
                ToolSpec(
                    name=tavily_extract.name,
                    description=tavily_extract.description,
                    args_model=tavily_extract.args_model,
                    fn=_bounded_extract,
                ),
            ]
        )
    return tools
