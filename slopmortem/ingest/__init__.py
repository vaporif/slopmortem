"""Ingest pipeline: gather → slop classify → fan-out → embed → upsert."""

from __future__ import annotations

from slopmortem.ingest._orchestrator import *  # noqa: F403  -- bounded by _orchestrator.__all__
