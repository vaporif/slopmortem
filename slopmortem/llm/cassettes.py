"""Cassette key derivation: structural hashes that identify an LLM call.

Lives under `slopmortem/llm/` (not `slopmortem/evals/`) because cassette
keys are a property of the LLM contract: they identify a unique
`(template, tools, response_format, model, prompt, system)` invocation.
The on-disk cassette format and the loaders/writers live in
`slopmortem.evals.cassettes`. This module does not import `evals`, so
`FakeLLMClient` and `RecordingLLMClient` can both depend on it without
cycles.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel


_PROMPT_HASH_LEN = 16
_TEXT_HASH_LEN = 16


def template_sha(
    template_text: str,
    tools: list[dict[str, object]] | None,
    response_format: type[BaseModel] | None,
) -> str:
    """Structural hash: template source + tools list + response_format schema.

    Returns a full hex digest (no truncation). Editing the template, the tool
    list, or the Pydantic response schema invalidates every cassette under
    that template, which is the cache-invalidation we want.
    """
    parts = [
        template_text,
        json.dumps(tools or [], sort_keys=True, separators=(",", ":")),
        json.dumps(
            response_format.model_json_schema() if response_format is not None else {},
            sort_keys=True,
            separators=(",", ":"),
        ),
    ]
    return hashlib.sha256("\x1e".join(parts).encode("utf-8")).hexdigest()


def llm_cassette_key(
    *, prompt: str, system: str | None, template_sha: str, model: str
) -> tuple[str, str, str]:
    r"""Compute the LLM cassette 3-tuple key.

    `\x1f` (ASCII unit separator) isolates the system block from the prompt
    so the two never alias regardless of content.
    """
    h = hashlib.sha256(((system or "") + "\x1f" + prompt).encode("utf-8")).hexdigest()
    return (template_sha, model, h[:_PROMPT_HASH_LEN])


def embed_cassette_key(*, text: str, model: str) -> tuple[str, str]:
    """Compute the embedding cassette 2-tuple key (shared by dense and sparse)."""
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return (model, h[:_TEXT_HASH_LEN])


class NoCannedEmbeddingError(BaseException):
    """Raised when an embedding cassette miss occurs under strict (canned-not-None) lookup.

    BaseException (not Exception) so resilient fan-out wrappers can't swallow
    it as an operational error — see ``NoCannedResponseError`` for the same
    rationale.
    """
