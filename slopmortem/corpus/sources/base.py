"""Source and Enricher Protocols.

* ``Source`` is a primary producer: ``fetch()`` -> ``AsyncIterable[RawEntry]``.
* ``Enricher`` is a secondary that fills fields on an existing ``RawEntry``.

Interface declarations consumed by concrete adapters (``curated.py``,
``hn_algolia.py``, ``crunchbase_csv.py``, ``wayback.py``). Tests exercise the
adapters directly; the Protocols themselves carry no behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterable

    from slopmortem.models import RawEntry


class Source(Protocol):
    """Produces ``RawEntry`` records. Primary input to corpus ingest."""

    def fetch(self) -> AsyncIterable[RawEntry]:
        """Yield raw entries asynchronously.

        Returns:
            An async iterable of ``RawEntry`` records, one per source document.
        """
        ...


class Enricher(Protocol):
    """Fills missing fields on an existing ``RawEntry``. Secondary input."""

    async def enrich(self, entry: RawEntry) -> RawEntry:
        """Return a (possibly) updated copy of *entry*.

        Args:
            entry: An entry produced by some upstream :class:`Source`.

        Returns:
            The same entry, optionally with previously-empty fields populated.
        """
        ...
