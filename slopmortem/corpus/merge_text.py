"""Deterministic combined-text rule for merging sections from multiple sources.

Sections are sorted by ``(reliability_rank, source_id)`` and joined with a stable
separator plus a markdown-style heading naming the source. Re-running ingest in
any order produces byte-identical output, which feeds a stable content_hash that
short-circuits re-extraction and re-embedding downstream.
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

    Sections are sorted ascending by ``(reliability_rank, source_id)`` so the
    most-reliable source appears first and ties break lexicographically.
    Each rendered section is preceded by ``## <source>:<source_id>`` so the
    section's provenance is visible in the merged document.
    """
    if not sections:
        return ""
    ordered = sorted(sections, key=lambda s: (s.reliability_rank, s.source_id))
    parts = [f"## {s.source}:{s.source_id}\n\n{s.text}" for s in ordered]
    return _SEPARATOR.join(parts)


def combined_hash(sections: list[Section]) -> str:
    """Return the first 16 hex chars of sha256 over :func:`combined_text` output.

    Used as the ``content_hash`` for skip-key derivation: bumping any field of
    any section reshuffles or changes the merged text and invalidates the hash.
    """
    return hashlib.sha256(combined_text(sections).encode("utf-8")).hexdigest()[:16]
