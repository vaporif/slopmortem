"""UTC time helpers used across modules."""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow_iso() -> str:
    """Return the current UTC time as a Z-suffixed ISO-8601 string."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
