from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from slopmortem.llm.client import CompletionResult


class NoCannedResponseError(KeyError):
    """Raised when a stage test calls FakeLLMClient with a (template_sha, model)
    pair that has no recorded fixture. Carries enough context for the failure
    message to point at the missing key without needing repro steps.
    """


@dataclass
class FakeResponse:
    text: str
    stop_reason: str = "stop"
    cost_usd: float = 0.0
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None

    def to_completion(self) -> CompletionResult:
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
    tools: list[Any] | None
    cache: bool
    response_format: dict[str, Any] | None
    extra_body: dict[str, Any] | None


@dataclass
class FakeLLMClient:
    """In-memory LLMClient stub that returns canned responses keyed on
    ``(prompt_template_sha, model)``.

    The template SHA is supplied by callers via ``extra_body['prompt_template_sha']``;
    stage tests load it from ``slopmortem.llm.prompts.prompt_template_sha(name)``
    so a prompt-text drift forces the fixture key to drift in lockstep.
    """

    canned: dict[tuple[str, str], FakeResponse | CompletionResult]
    default_model: str
    calls: list[_Call] = field(default_factory=list)

    async def complete(
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
        eff_model = model or self.default_model
        template_sha: str | None = None
        if extra_body and "prompt_template_sha" in extra_body:
            template_sha = str(extra_body["prompt_template_sha"])
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
