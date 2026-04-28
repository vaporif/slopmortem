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


def render_prompt(name: str, **vars: Any) -> str:
    return _env.get_template(f"{name}.j2").render(**vars)


def render_blocks(name: str, **vars: Any) -> dict[str, str]:
    """Render `system` and `user` blocks separately for cache_control handling."""
    template = _env.get_template(f"{name}.j2")
    context = template.new_context(vars)
    blocks = {}
    for block_name in ("system", "user"):
        if block_name in template.blocks:
            blocks[block_name] = "".join(template.blocks[block_name](context))
    return blocks


def prompt_template_sha(name: str) -> str:
    return hashlib.sha256(_PROMPT_DIR.joinpath(f"{name}.j2").read_bytes()).hexdigest()[:16]
