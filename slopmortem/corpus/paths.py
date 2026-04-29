"""Validated, traversal-safe path construction for raw, canonical, and quarantine docs."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, cast

_TEXT_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_CONTENT_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_RE = re.compile(r"^[a-z0-9_]{1,32}$")

Kind = Literal["raw", "canonical", "quarantine"]


def _raw_candidate(base: Path, text_id: str | None, source: str | None) -> Path:
    if not source:
        msg = "raw kind requires source"
        raise ValueError(msg)
    if text_id is None or not _TEXT_ID_RE.match(text_id):
        msg = f"invalid text_id: {text_id!r}"
        raise ValueError(msg)
    if not _SOURCE_RE.match(source):
        msg = f"invalid source: {source!r}"
        raise ValueError(msg)
    return base / "raw" / source / f"{text_id}.md"


def _canonical_candidate(base: Path, text_id: str | None, source: str | None) -> Path:
    if source is not None:
        msg = "canonical kind forbids source"
        raise ValueError(msg)
    if text_id is None or not _TEXT_ID_RE.match(text_id):
        msg = f"invalid text_id: {text_id!r}"
        raise ValueError(msg)
    return base / "canonical" / f"{text_id}.md"


def _quarantine_candidate(base: Path, content_sha256: str | None) -> Path:
    if content_sha256 is None or not _CONTENT_SHA_RE.match(content_sha256):
        msg = f"invalid content_sha256: {content_sha256!r}"
        raise ValueError(msg)
    return base / "quarantine" / f"{content_sha256}.md"


def safe_path(
    base: Path,
    *,
    kind: Kind,
    text_id: str | None = None,
    source: str | None = None,
    content_sha256: str | None = None,
) -> Path:
    """Build a validated path under *base* for the given doc *kind*; refuse traversal."""
    base = Path(base).resolve()
    # Cast lets us inspect at runtime — callers may pass an unknown string and
    # we want a friendly ValueError rather than a Literal-typing assert.
    kind_str = cast("str", kind)
    if kind_str == "raw":
        candidate = _raw_candidate(base, text_id, source)
    elif kind_str == "canonical":
        candidate = _canonical_candidate(base, text_id, source)
    elif kind_str == "quarantine":
        candidate = _quarantine_candidate(base, content_sha256)
    else:
        msg = f"unknown kind: {kind!r}"
        raise ValueError(msg)

    resolved = candidate.resolve()
    if not resolved.is_relative_to(base):
        msg = f"path escapes base: {resolved} not under {base}"
        raise ValueError(msg)
    return resolved
