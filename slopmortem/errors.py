"""Pipeline-stage error types callers want to ``except`` by name."""

from __future__ import annotations


class RerankLengthError(RuntimeError):
    """``llm_rerank``'s ``ranked`` array has the wrong length.

    Strict-mode JSON schema constrains entry shape but not array length, so
    we re-check post-parse instead of fanning out the wrong number of synth
    calls.
    """
