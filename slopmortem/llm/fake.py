"""In-memory LLMClient stub for stage tests; canned responses keyed by fixture."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from slopmortem.llm.cassettes import llm_cassette_key
from slopmortem.llm.client import CompletionResult

if TYPE_CHECKING:
    from collections.abc import Mapping


class NoCannedResponseError(KeyError):
    """Raised when FakeLLMClient can't find a canned reply for the given key.

    The error message names the missing ``(template_sha, model, prompt_hash)``
    so you can find the gap without a repro.
    """


@dataclass
class FakeResponse:
    """Canned response that FakeLLMClient turns into a CompletionResult when called."""

    text: str
    stop_reason: str = "stop"
    cost_usd: float = 0.0
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None

    def to_completion(self) -> CompletionResult:
        """Convert this fixture into a CompletionResult."""
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
    prompt_hash: str | None = None
    max_tokens: int | None = None


@dataclass
class FakeLLMClient:
    r"""In-memory LLMClient stub keyed on ``(prompt_template_sha, model, prompt_hash)``.

    Callers pass the template SHA via ``extra_body['prompt_template_sha']``.
    Stage tests load it from
    ``slopmortem.llm.prompts.prompt_template_sha(name)`` so any change in
    prompt text changes the fixture key too. The ``prompt_hash`` slot is the
    first 16 hex chars of ``sha256(system + '\x1f' + prompt)`` (via
    :func:`slopmortem.llm.cassettes.llm_cassette_key`); tests can override it
    by passing ``extra_body['prompt_hash']`` directly.
    """

    canned: Mapping[tuple[str, str, str], FakeResponse | CompletionResult]
    default_model: str
    calls: list[_Call] = field(default_factory=list)

    async def complete(  # noqa: PLR0913 - mirrors LLMClient.complete public signature
        self,
        prompt: str,
        *,
        system: str | None = None,
        tools: list[Any] | None = None,  # pyright: ignore[reportExplicitAny]
        model: str | None = None,
        cache: bool = False,
        response_format: dict[str, Any] | None = None,  # pyright: ignore[reportExplicitAny]
        extra_body: dict[str, Any] | None = None,  # pyright: ignore[reportExplicitAny]
        max_tokens: int | None = None,
    ) -> CompletionResult:
        """Look up a canned response keyed by ``(prompt_template_sha, model, prompt_hash)``."""
        eff_model = model or self.default_model
        template_sha: str | None = None
        if extra_body and "prompt_template_sha" in extra_body:
            template_sha = str(extra_body["prompt_template_sha"])  # pyright: ignore[reportAny]
        if template_sha is None:
            msg = (
                "FakeLLMClient requires extra_body['prompt_template_sha']; "
                f"none supplied for model {eff_model!r}"
            )
            raise NoCannedResponseError(msg)
        # Compute prompt_hash from prompt+system; tests can pin a specific
        # hash by setting extra_body["prompt_hash"].
        prompt_hash: str
        if extra_body and "prompt_hash" in extra_body:
            prompt_hash = str(extra_body["prompt_hash"])  # pyright: ignore[reportAny]
        else:
            _, _, prompt_hash = llm_cassette_key(
                prompt=prompt,
                system=system,
                template_sha=template_sha,
                model=eff_model,
            )
        self.calls.append(
            _Call(
                prompt=prompt,
                model=eff_model,
                template_sha=template_sha,
                prompt_hash=prompt_hash,
                system=system,
                tools=tools,
                cache=cache,
                response_format=response_format,
                extra_body=extra_body,
                max_tokens=max_tokens,
            )
        )
        key = (template_sha, eff_model, prompt_hash)
        if key not in self.canned:
            msg = f"no canned response for key={key!r}; recorded keys: {sorted(self.canned)}"
            raise NoCannedResponseError(msg)
        item = self.canned[key]
        if isinstance(item, CompletionResult):
            return item
        return item.to_completion()
