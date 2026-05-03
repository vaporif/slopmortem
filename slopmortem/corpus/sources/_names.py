"""Canonical source-identifier strings.

These are the values emitted in `slopmortem.models.RawEntry.source`.
They double as keys for the reliability rank table and the pre-vetted set,
so they live in one module to keep those uses in lockstep.
"""

from __future__ import annotations

from typing import Final

SOURCE_CURATED: Final = "curated"
SOURCE_HN_ALGOLIA: Final = "hn_algolia"
SOURCE_CRUNCHBASE_CSV: Final = "crunchbase_csv"
