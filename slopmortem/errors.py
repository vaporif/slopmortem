"""Top-level error types raised by pipeline stages.

Stage-level errors the orchestrator wants to distinguish from a generic
``RuntimeError`` live here so callers can ``except`` them by name without a
brittle string match. Adding a new error? Subclass ``RuntimeError`` unless the
call site needs a more specific base.
"""

from __future__ import annotations


class RerankLengthError(RuntimeError):
    """Raised when ``llm_rerank``'s ``ranked`` length is wrong.

    Expected: ``min(N_synthesize, len(candidates))``. Strict-mode JSON
    schema constrains entry shape but not array length, so we re-check
    post-parse and surface a typed error instead of letting an off-by-one
    fan out fewer or more synth calls than the operator expected.
    """
