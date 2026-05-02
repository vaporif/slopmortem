"""Deterministic combined-text rule for merging sections from multiple sources.

Byte-identical output across ingest orderings → stable content_hash skips
re-extraction and re-embedding downstream.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

_SEPARATOR = "\n\n---\n\n"


@dataclass(frozen=True, slots=True)
class Section:
    text: str
    reliability_rank: int
    source_id: str
    source: str


def combined_text(sections: list[Section]) -> str:
    """Return merged text with ``## <source>:<source_id>`` provenance heads.

    Sections are sorted by ``(reliability_rank, source_id)``.
    """
    if not sections:
        return ""
    ordered = sorted(sections, key=lambda s: (s.reliability_rank, s.source_id))
    parts = [f"## {s.source}:{s.source_id}\n\n{s.text}" for s in ordered]
    return _SEPARATOR.join(parts)


def combined_hash(sections: list[Section]) -> str:
    """First 16 hex chars of sha256 over :func:`combined_text`.

    Drives the skip-key ``content_hash``.
    """
    return hashlib.sha256(combined_text(sections).encode("utf-8")).hexdigest()[:16]
