"""Top-level error types raised by pipeline stages.

Stage-level errors the orchestrator wants to distinguish from a generic
``RuntimeError`` live here so callers can ``except`` them by name without a
brittle string match. Adding a new error? Subclass ``RuntimeError`` unless the
call site needs a more specific base.
"""

from __future__ import annotations


class RerankLengthError(RuntimeError):
    """Raised when ``llm_rerank``'s ``ranked`` array length is not ``N_synthesize``.

    Strict-mode JSON schema constrains each entry's shape but not the array
    length, so we re-validate post-parse and surface a typed error instead of
    letting an off-by-one propagate into synthesis — which would silently fan
    out fewer or more calls than the operator expected.
    """
