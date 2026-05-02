"""Cassette key derivation: structural hashes that identify an LLM call.

Lives under ``slopmortem/llm/`` so ``FakeLLMClient`` and ``RecordingLLMClient``
can both depend on it without dragging ``evals`` into prod (one-way:
evals → llm). On-disk format lives in ``slopmortem.evals.cassettes``.
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

    Editing the template, tool list, or Pydantic response schema invalidates
    every cassette under that template.
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
    r"""LLM cassette 3-tuple key. ``\x1f`` separates system from prompt so the two never alias."""
    h = hashlib.sha256(((system or "") + "\x1f" + prompt).encode("utf-8")).hexdigest()
    return (template_sha, model, h[:_PROMPT_HASH_LEN])


def embed_cassette_key(*, text: str, model: str) -> tuple[str, str]:
    """Compute the embedding cassette 2-tuple key (shared by dense and sparse)."""
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return (model, h[:_TEXT_HASH_LEN])


class NoCannedEmbeddingError(BaseException):
    """Embedding cassette miss under strict lookup.

    Inherits ``BaseException`` so resilient fan-out wrappers (which catch
    ``Exception``) can't swallow it as an operational error.
    """
