"""Source adapters and enrichers. They produce ``RawEntry`` for ingest."""

from __future__ import annotations

from slopmortem.corpus.sources.base import Enricher, Source

__all__ = ["Enricher", "Source"]
