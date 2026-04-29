"""Closed enum of span event names the tracer emits for security and health monitoring."""

from enum import StrEnum


class SpanEvent(StrEnum):
    """Security- and health-relevant events written as Laminar span attributes."""

    PROMPT_INJECTION_ATTEMPTED = "prompt_injection_attempted"
    TOOL_ALLOWLIST_VIOLATION = "tool_allowlist_violation"
    PARENT_SUBSIDIARY_SUSPECTED = "entity.parent_subsidiary_suspected"
    CUSTOM_ALIAS_SUSPECTED = "entity.custom_alias_suspected"
    CORPUS_POISONING_WARNING = "corpus.poisoning_warning"
    CORPUS_DOC_TRUNCATED = "corpus.doc_truncated"
    BUDGET_EXCEEDED = "budget_exceeded"
    CACHE_WARM_FAILED = "cache_warm_failed"
    SSRF_BLOCKED = "ssrf_blocked"
    RESOLVER_FLIP_DETECTED = "resolver_flip_detected"
    CACHE_READ_RATIO_LOW = "cache_read_ratio_low"
    SLOP_QUARANTINED = "slop_quarantined"
    SOURCE_FETCH_FAILED = "source_fetch_failed"
    INGEST_ENTRY_FAILED = "ingest_entry_failed"
    RECONCILE_REPAIR_APPLIED = "reconcile_repair_applied"
