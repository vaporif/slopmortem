"""In-memory LLMClient stub for stage tests — fixture-keyed canned responses."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from slopmortem.llm.client import CompletionResult

if TYPE_CHECKING:
    from collections.abc import Mapping


class NoCannedResponseError(KeyError):
    """Raised when FakeLLMClient cannot find a canned reply for the given key.

    Carries enough context for the failure message to point at the missing
    ``(template_sha, model)`` without needing repro steps.
    """


@dataclass
class FakeResponse:
    """A canned response the FakeLLMClient renders into a CompletionResult on demand."""

    text: str
    stop_reason: str = "stop"
    cost_usd: float = 0.0
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None

    def to_completion(self) -> CompletionResult:
        """Materialize this fixture into a real CompletionResult."""
        return CompletionResult(
            text=self.text,
            stop_reason=self.stop_reason,
            cost_usd=self.cost_usd,
            cache_read_tokens=self.cache_read_tokens,
            cache_creation_tokens=self.cache_creation_tokens,
        )


@dataclass
class _Call:
    prompt: str
    model: str
    template_sha: str | None
    system: str | None
    tools: list[Any] | None  # pyright: ignore[reportExplicitAny]
    cache: bool
    response_format: dict[str, Any] | None  # pyright: ignore[reportExplicitAny]
    extra_body: dict[str, Any] | None  # pyright: ignore[reportExplicitAny]


@dataclass
class FakeLLMClient:
    """In-memory LLMClient stub keyed on ``(prompt_template_sha, model)``.

    The template SHA is supplied by callers via ``extra_body['prompt_template_sha']``;
    stage tests load it from ``slopmortem.llm.prompts.prompt_template_sha(name)``
    so a prompt-text drift forces the fixture key to drift in lockstep.
    """

    canned: Mapping[tuple[str, str], FakeResponse | CompletionResult]
    default_model: str
    calls: list[_Call] = field(default_factory=list)

    async def complete(  # noqa: PLR0913 — mirrors LLMClient.complete public signature
        self,
        prompt: str,
        *,
        system: str | None = None,
        tools: list[Any] | None = None,  # pyright: ignore[reportExplicitAny]
        model: str | None = None,
        cache: bool = False,
        response_format: dict[str, Any] | None = None,  # pyright: ignore[reportExplicitAny]
        extra_body: dict[str, Any] | None = None,  # pyright: ignore[reportExplicitAny]
    ) -> CompletionResult:
        """Look up a canned response keyed by ``(prompt_template_sha, model)``."""
        eff_model = model or self.default_model
        template_sha: str | None = None
        if extra_body and "prompt_template_sha" in extra_body:
            template_sha = str(extra_body["prompt_template_sha"])  # pyright: ignore[reportAny]
        self.calls.append(
            _Call(
                prompt=prompt,
                model=eff_model,
                template_sha=template_sha,
                system=system,
                tools=tools,
                cache=cache,
                response_format=response_format,
                extra_body=extra_body,
            )
        )
        if template_sha is None:
            msg = (
                "FakeLLMClient requires extra_body['prompt_template_sha']; "
                f"none supplied for model {eff_model!r}"
            )
            raise NoCannedResponseError(msg)
        key = (template_sha, eff_model)
        if key not in self.canned:
            msg = (
                f"no canned response for prompt_template_sha={template_sha!r}, "
                f"model={eff_model!r}; recorded keys: {sorted(self.canned)}"
            )
            raise NoCannedResponseError(msg)
        item = self.canned[key]
        if isinstance(item, CompletionResult):
            return item
        return item.to_completion()
