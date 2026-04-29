"""Closed enum of span event names emitted to the tracer for security/health monitoring."""

from __future__ import annotations

from enum import StrEnum


class SpanEvent(StrEnum):
    """Security- and health-relevant events emitted as Laminar span attributes."""

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
