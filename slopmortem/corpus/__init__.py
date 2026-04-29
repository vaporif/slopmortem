"""Corpus storage, paths, retrieval, and tool-call implementations."""

from __future__ import annotations

from slopmortem.corpus.chunk import CHUNK_STRATEGY_VERSION, Chunk, chunk_markdown
from slopmortem.corpus.disk import (
    read_canonical,
    write_canonical_atomic,
    write_raw_atomic,
)
from slopmortem.corpus.merge import MergeJournal
from slopmortem.corpus.qdrant_store import QdrantCorpus, ensure_collection
from slopmortem.corpus.reconcile import (
    DRIFT_CLASSES,
    ReconcileReport,
    ReconcileRow,
    reconcile,
)

__all__ = [
    "CHUNK_STRATEGY_VERSION",
    "DRIFT_CLASSES",
    "Chunk",
    "MergeJournal",
    "QdrantCorpus",
    "ReconcileReport",
    "ReconcileRow",
    "chunk_markdown",
    "ensure_collection",
    "read_canonical",
    "reconcile",
    "write_canonical_atomic",
    "write_raw_atomic",
]
