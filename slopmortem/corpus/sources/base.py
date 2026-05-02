"""Source and Enricher Protocols.

``Source.fetch()`` is the primary producer; ``Enricher.enrich()`` fills
fields on an existing ``RawEntry``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterable

    from slopmortem.models import RawEntry


class Source(Protocol):
    """Primary producer of ``RawEntry`` records."""

    def fetch(self) -> AsyncIterable[RawEntry]: ...


class Enricher(Protocol):
    """Fills missing fields on an existing ``RawEntry``."""

    async def enrich(self, entry: RawEntry) -> RawEntry: ...
