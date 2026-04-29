"""Atomic markdown read/write for the raw and canonical post-mortem trees.

Writes go to ``<path>.tmp`` then :meth:`Path.replace` (POSIX-atomic). Front
matter is rendered as YAML between ``---`` delimiters. Path construction
always goes through :func:`safe_path`: no concatenation, no traversal.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import TYPE_CHECKING

import yaml

from slopmortem.corpus.paths import safe_path

if TYPE_CHECKING:
    from pathlib import Path

# Front-matter values are JSON-y (str / int / float / bool / list / dict / None).
# Pyright's `reportExplicitAny` blocks the obvious `Any` annotation, so we use
# `object` and round-trip through `yaml.safe_dump` which accepts anything.
FrontMatter = dict[str, object]


def _render(body: str, front_matter: FrontMatter) -> str:
    """Render YAML front-matter and body into a single markdown string."""
    fm = yaml.safe_dump(front_matter, sort_keys=True, default_flow_style=False).strip()
    return f"---\n{fm}\n---\n{body}"


def _write_sync(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique tmp suffix per call so two concurrent writes to the same path
    # don't share a tmp filename and clobber each other's rename.
    tmp = path.with_suffix(f"{path.suffix}.{secrets.token_hex(8)}.tmp")
    try:
        tmp.write_text(contents, encoding="utf-8")
        tmp.replace(path)
    finally:
        # On success, replace already renamed the tmp file. On failure, clean
        # it up so we don't leak a .tmp on disk.
        if tmp.exists():
            tmp.unlink()


async def write_canonical_atomic(
    base: Path,
    text_id: str,
    body: str,
    *,
    front_matter: FrontMatter | None = None,
) -> None:
    """Atomically write the canonical merged markdown for *text_id*."""
    path = safe_path(base, kind="canonical", text_id=text_id)
    contents = _render(body, front_matter or {})
    await asyncio.to_thread(_write_sync, path, contents)


async def write_raw_atomic(
    base: Path,
    text_id: str,
    source: str,
    body: str,
    *,
    front_matter: FrontMatter | None = None,
) -> None:
    """Atomically write the per-source raw markdown for *text_id*."""
    path = safe_path(base, kind="raw", text_id=text_id, source=source)
    contents = _render(body, front_matter or {})
    await asyncio.to_thread(_write_sync, path, contents)


def read_canonical(base: Path, text_id: str) -> str:
    """Read the full markdown (front-matter + body) for canonical *text_id*."""
    path = safe_path(base, kind="canonical", text_id=text_id)
    return path.read_text(encoding="utf-8")
