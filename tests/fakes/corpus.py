"""Test fakes for the ingest write-side :class:`Corpus` protocol."""

from __future__ import annotations

from dataclasses import dataclass, field

from slopmortem.ingest import _Point


@dataclass
class InMemoryCorpus:
    """In-memory write-side corpus used by ingest tests; not used in production."""

    points: list[_Point] = field(default_factory=list)

    async def upsert_chunk(self, point: object) -> None:
        if not isinstance(point, _Point):
            msg = f"InMemoryCorpus expects _Point, got {type(point).__name__}"
            raise TypeError(msg)
        self.points.append(point)

    async def has_chunks(self, canonical_id: str) -> bool:
        return any(p.payload.get("canonical_id") == canonical_id for p in self.points)

    async def delete_chunks_for_canonical(self, canonical_id: str) -> None:
        self.points = [p for p in self.points if p.payload.get("canonical_id") != canonical_id]
