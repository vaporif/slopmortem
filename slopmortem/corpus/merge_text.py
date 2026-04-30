"""Deterministic combined-text rule for merging sections from multiple sources.

Sections sort by ``(reliability_rank, source_id)`` and join with a stable
separator plus a markdown heading naming the source. Re-running ingest in
any order yields byte-identical output, which feeds a stable content_hash
that skips re-extraction and re-embedding downstream.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

_SEPARATOR = "\n\n---\n\n"


@dataclass(frozen=True, slots=True)
class Section:
    """One source's contribution to a canonical entry's combined text."""

    text: str
    reliability_rank: int
    source_id: str
    source: str


def combined_text(sections: list[Section]) -> str:
    """Return the deterministic merged text for *sections*.

    Sort ascending by ``(reliability_rank, source_id)``, most-reliable
    source first, ties broken lexicographically. Each rendered section is
    preceded by ``## <source>:<source_id>`` so provenance shows up in the
    merged document.
    """
    if not sections:
        return ""
    ordered = sorted(sections, key=lambda s: (s.reliability_rank, s.source_id))
    parts = [f"## {s.source}:{s.source_id}\n\n{s.text}" for s in ordered]
    return _SEPARATOR.join(parts)


def combined_hash(sections: list[Section]) -> str:
    """Return the first 16 hex chars of sha256 over :func:`combined_text` output.

    Used as the ``content_hash`` for skip-key derivation: bumping any field
    of any section changes the merged text and invalidates the hash.
    """
    return hashlib.sha256(combined_text(sections).encode("utf-8")).hexdigest()[:16]
