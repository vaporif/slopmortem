"""Tool signature contract tests (plan §9.3).

Two invariants:

1. Pydantic args model -> OpenAI input schema -> back to args round-trips
   without drift. The schema ships to the LLM; the model validates the
   LLM's tool call, so the two must agree.
2. The tool implementation module imports nothing from ``subprocess``,
   ``os.system``, ``shutil.rmtree``, or ``shutil.copy``. Corpus tools have
   no reason to shell out, and the synthesis path is sandboxed by
   `safe_get` / `safe_path`.
"""

from __future__ import annotations

import ast
import inspect

from slopmortem.corpus import _tools_impl
from slopmortem.corpus._tools_impl import get_post_mortem, search_corpus
from slopmortem.llm import to_openai_input_schema

BANNED_MODULES = frozenset({"subprocess"})
BANNED_ATTRS = frozenset({("os", "system"), ("shutil", "rmtree"), ("shutil", "copy")})


def test_tool_signatures_round_trip():
    """Pydantic args -> SDK schema -> back to args; no drift."""
    for tool in (get_post_mortem, search_corpus):
        schema = to_openai_input_schema(tool.args_model)
        assert isinstance(schema, dict)
        # Round-trip a sample.
        if tool.name == "get_post_mortem":
            sample: dict[str, object] = {"canonical_id": "acme.com"}
        else:
            sample = {"q": "scrap", "limit": 3}
        parsed = tool.args_model.model_validate(sample)
        assert parsed.model_dump(exclude_none=True).keys() <= sample.keys() | {
            "facets",
            "limit",
            "max_chars",
        }


def _check_import(node: ast.Import) -> list[str]:
    return [
        f"import {alias.name}"
        for alias in node.names
        if alias.name.split(".", 1)[0] in BANNED_MODULES
    ]


def _check_import_from(node: ast.ImportFrom) -> list[str]:
    found: list[str] = []
    if node.module and node.module.split(".", 1)[0] in BANNED_MODULES:
        found.append(f"from {node.module} import ...")
    for mod, attr in BANNED_ATTRS:
        if node.module == mod and any(a.name == attr for a in node.names):
            found.append(f"from {mod} import {attr}")
    return found


def _check_attribute(node: ast.Attribute) -> list[str]:
    if isinstance(node.value, ast.Name) and (node.value.id, node.attr) in BANNED_ATTRS:
        return [f"{node.value.id}.{node.attr}"]
    return []


def test_no_subprocess_imports_in_tools():
    """AST-based: defeats `import subprocess as sp` and similar aliasing tricks."""
    tree = ast.parse(inspect.getsource(_tools_impl))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            violations.extend(_check_import(node))
        elif isinstance(node, ast.ImportFrom):
            violations.extend(_check_import_from(node))
        elif isinstance(node, ast.Attribute):
            violations.extend(_check_attribute(node))
    assert not violations, f"banned references: {violations}"
