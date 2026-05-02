# T1.1 verification (date 2026-05-02): submodule audit results
#   store        = TYPE_CHECKING-only (3 sites: pipeline.py, cli.py, stages/retrieve.py)
#   summarize    = external (1 site: tests/corpus/test_summarize.py)
#   alias_graph  = internal-only (only sibling import in qdrant_store.py)
#   embed_sparse = external (lazy/runtime sites: ingest.py, evals/recording_helper.py,
#                  stages/retrieve.py + test monkeypatches in test_observe_redaction.py
#                  and test_pipeline_e2e.py — none top-level `from … import`)
#   merge_text   = external (2 sites: ingest.py, tests/corpus/test_merge_deterministic.py)
#   tools_impl   = external (cli.py + tests: test_tool_signature_contract,
#                  test_tavily_tools, test_synthesis_tools)
# T1.7 reads this to decide which modules are safe to underscore.
"""Corpus storage: paths, retrieval, and tool-call implementations."""

from __future__ import annotations

from slopmortem.corpus.chunk import CHUNK_STRATEGY_VERSION as CHUNK_STRATEGY_VERSION
from slopmortem.corpus.chunk import Chunk as Chunk
from slopmortem.corpus.chunk import chunk_markdown as chunk_markdown
from slopmortem.corpus.disk import read_canonical as read_canonical
from slopmortem.corpus.disk import write_canonical_atomic as write_canonical_atomic
from slopmortem.corpus.disk import write_raw_atomic as write_raw_atomic
from slopmortem.corpus.entity_resolution import resolve_entity as resolve_entity
from slopmortem.corpus.extract import extract_clean as extract_clean
from slopmortem.corpus.merge import MergeJournal as MergeJournal
from slopmortem.corpus.merge_text import Section as Section
from slopmortem.corpus.merge_text import combined_hash as combined_hash
from slopmortem.corpus.merge_text import combined_text as combined_text
from slopmortem.corpus.paths import safe_path as safe_path
from slopmortem.corpus.qdrant_store import QdrantCorpus as QdrantCorpus
from slopmortem.corpus.qdrant_store import ensure_collection as ensure_collection
from slopmortem.corpus.reclassify import reclassify_quarantined as reclassify_quarantined
from slopmortem.corpus.reconcile import DRIFT_CLASSES as DRIFT_CLASSES
from slopmortem.corpus.reconcile import ReconcileReport as ReconcileReport
from slopmortem.corpus.reconcile import ReconcileRow as ReconcileRow
from slopmortem.corpus.reconcile import reconcile as reconcile
from slopmortem.corpus.summarize import summarize_for_rerank as summarize_for_rerank
from slopmortem.corpus.tools_impl import set_query_corpus as set_query_corpus

__all__ = [
    "CHUNK_STRATEGY_VERSION",
    "DRIFT_CLASSES",
    "Chunk",
    "MergeJournal",
    "QdrantCorpus",
    "ReconcileReport",
    "ReconcileRow",
    "Section",
    "chunk_markdown",
    "combined_hash",
    "combined_text",
    "ensure_collection",
    "extract_clean",
    "read_canonical",
    "reclassify_quarantined",
    "reconcile",
    "resolve_entity",
    "safe_path",
    "set_query_corpus",
    "summarize_for_rerank",
    "write_canonical_atomic",
    "write_raw_atomic",
]
