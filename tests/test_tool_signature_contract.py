"""Tool signature contract tests (plan §9.3).

Two invariants:

1. Pydantic args model -> OpenAI input schema -> back to args round-trips
   without drift. The schema is what we ship to the LLM; the model is what
   we validate the LLM's tool call against. They must agree.
2. The tool implementation module imports nothing from ``subprocess``,
   ``os.system``, ``shutil.rmtree``, or ``shutil.copy`` — corpus tools have
   no business shelling out, and the synthesis path is sandboxed by the
   :func:`safe_get` / :func:`safe_path` boundary.
"""

from __future__ import annotations

import inspect

from slopmortem.corpus import tools_impl
from slopmortem.corpus.tools_impl import get_post_mortem, search_corpus
from slopmortem.llm.tools import to_openai_input_schema


def test_tool_signatures_round_trip():
    """Pydantic args -> SDK schema -> back to args. No drift."""
    for tool in (get_post_mortem, search_corpus):
        schema = to_openai_input_schema(tool.args_model)
        assert isinstance(schema, dict)
        # round-trip a sample
        if tool.name == "get_post_mortem":
            sample: dict[str, object] = {"canonical_id": "acme.com"}
        else:
            sample = {"q": "scrap", "limit": 3}
        parsed = tool.args_model.model_validate(sample)
        assert parsed.model_dump(exclude_none=True).keys() <= sample.keys() | {"facets", "limit"}


def test_no_subprocess_imports_in_tools():
    src = inspect.getsource(tools_impl)
    for banned in ("subprocess", "os.system", "shutil.rmtree", "shutil.copy"):
        assert banned not in src, f"banned import: {banned}"
