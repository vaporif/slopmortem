"""Top-level error types raised by pipeline stages.

Stage-level errors that the orchestrator wants to distinguish from generic
``RuntimeError`` live here so callers can ``except`` them by name without a
brittle string match. Adding a new error: prefer subclassing ``RuntimeError``
unless the call site needs a more specific base.
"""


class RerankLengthError(RuntimeError):
    """Raised when ``llm_rerank``'s ``ranked`` array length is not ``N_synthesize``.

    Strict-mode JSON schema constrains the shape of each entry but cannot
    constrain the array length, so we re-validate post-parse and surface
    a typed error rather than letting an off-by-one propagate into
    synthesis (which would silently fan out fewer or more calls than the
    operator expected).
    """
