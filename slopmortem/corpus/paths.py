from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

_TEXT_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_CONTENT_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

Kind = Literal["raw", "canonical", "quarantine"]


def safe_path(
    base: Path,
    *,
    kind: Kind,
    text_id: str | None = None,
    source: str | None = None,
    content_sha256: str | None = None,
) -> Path:
    base = Path(base).resolve()
    if kind == "raw":
        if not source:
            msg = "raw kind requires source"
            raise ValueError(msg)
        if text_id is None or not _TEXT_ID_RE.match(text_id):
            msg = f"invalid text_id: {text_id!r}"
            raise ValueError(msg)
        if not re.match(r"^[a-z0-9_]{1,32}$", source):
            msg = f"invalid source: {source!r}"
            raise ValueError(msg)
        candidate = base / "raw" / source / f"{text_id}.md"
    elif kind == "canonical":
        if source is not None:
            msg = "canonical kind forbids source"
            raise ValueError(msg)
        if text_id is None or not _TEXT_ID_RE.match(text_id):
            msg = f"invalid text_id: {text_id!r}"
            raise ValueError(msg)
        candidate = base / "canonical" / f"{text_id}.md"
    elif kind == "quarantine":
        if content_sha256 is None or not _CONTENT_SHA_RE.match(content_sha256):
            msg = f"invalid content_sha256: {content_sha256!r}"
            raise ValueError(msg)
        candidate = base / "quarantine" / f"{content_sha256}.md"
    else:
        msg = f"unknown kind: {kind!r}"
        raise ValueError(msg)

    resolved = candidate.resolve()
    if not resolved.is_relative_to(base):
        msg = f"path escapes base: {resolved} not under {base}"
        raise ValueError(msg)
    return resolved
