"""Jinja2 prompt rendering plus a stable template SHA used to pin fixtures."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

_PROMPT_DIR = Path(__file__).parent
_TAXONOMY_PATH = Path(__file__).resolve().parents[2] / "corpus" / "taxonomy.yml"

_env = Environment(
    loader=FileSystemLoader(_PROMPT_DIR),
    autoescape=select_autoescape(disabled_extensions=("j2",), default=False),
    keep_trailing_newline=True,
    undefined=StrictUndefined,
)
_env.globals["taxonomy"] = yaml.safe_load(_TAXONOMY_PATH.read_text())


def render_prompt(
    name: str,
    **template_vars: Any,  # pyright: ignore[reportAny, reportExplicitAny]  # Jinja accepts arbitrary template context
) -> str:
    """Render the named ``.j2`` template fully, returning the joined output text."""
    return _env.get_template(f"{name}.j2").render(**template_vars)


def render_blocks(
    name: str,
    **template_vars: Any,  # pyright: ignore[reportAny, reportExplicitAny]  # Jinja accepts arbitrary template context
) -> dict[str, str]:
    """Render ``system`` and ``user`` blocks separately for cache_control handling."""
    template = _env.get_template(f"{name}.j2")
    context = template.new_context(template_vars)
    blocks: dict[str, str] = {}
    for block_name in ("system", "user"):
        if block_name in template.blocks:
            blocks[block_name] = "".join(template.blocks[block_name](context))
    return blocks


def prompt_template_sha(name: str) -> str:
    """First 16 hex chars of sha256 over the ``.j2`` source. Used as the fixture key."""
    return hashlib.sha256(_PROMPT_DIR.joinpath(f"{name}.j2").read_bytes()).hexdigest()[:16]
