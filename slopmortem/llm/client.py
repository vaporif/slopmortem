"""LLMClient Protocol — the chat-completion shape every backend (real, fake, cassette) honors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from pydantic import BaseModel


@dataclass
class CompletionResult:
    """Single completion turn: text, stop reason, optional Pydantic model, cost/cache stats."""

    text: str
    stop_reason: str
    parsed: BaseModel | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    cost_usd: float = 0.0


@runtime_checkable
class LLMClient(Protocol):
    """The async chat-completion contract every LLM backend (real or fake) implements."""

    # ``Any`` here is intentional: tools/response_format/extra_body are SDK passthroughs.
    async def complete(  # type: ignore[explicit-any]  # noqa: PLR0913 — mirrors OpenAI chat.create
        self,
        prompt: str,
        *,
        system: str | None = None,
        tools: list[Any] | None = None,
        model: str | None = None,
        cache: bool = False,
        response_format: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> CompletionResult:
        """Run a completion; the implementation may handle tool calls and retries internally."""
        ...
