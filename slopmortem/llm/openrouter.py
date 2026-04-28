from __future__ import annotations

import asyncio
import json
import random
from typing import TYPE_CHECKING, Any, TypeVar

import anyio

from slopmortem.llm.client import CompletionResult
from slopmortem.tracing.events import SpanEvent

if TYPE_CHECKING:
    from collections.abc import Awaitable, Coroutine, Iterable

    from slopmortem.budget import Budget
    from slopmortem.models import ToolSpec


class MidStreamError(Exception):
    """Raised when an SSE chunk arrives at HTTP 200 with finish_reason='error'.

    Carries the raw error payload so the retry layer can decide whether the
    error.code is transient (e.g. ``overloaded_error``) or fatal.
    """

    def __init__(self, error: Any) -> None:
        super().__init__(str(error))
        self.error = error or {}

    @property
    def code(self) -> str:
        if isinstance(self.error, dict):
            return str(self.error.get("code", ""))
        return str(getattr(self.error, "code", ""))


_TRANSIENT_MIDSTREAM_CODES = frozenset({"overloaded_error"})


T = TypeVar("T")


async def gather_with_limit(
    coros: Iterable[Coroutine[Any, Any, T]],
    limit: int,
) -> list[T | BaseException]:
    """Run *coros* concurrently with at most *limit* in flight.

    Wraps ``asyncio.gather(..., return_exceptions=True)`` with an
    ``anyio.CapacityLimiter`` so callers can cap parallel OpenRouter calls
    against ``config.ingest_concurrency`` without writing the bookkeeping
    themselves.
    """
    limiter = anyio.CapacityLimiter(limit)

    async def _run(coro: Coroutine[Any, Any, T]) -> T:
        async with limiter:
            return await coro

    return await asyncio.gather(*(_run(c) for c in coros), return_exceptions=True)


class OpenRouterClient:
    def __init__(
        self,
        *,
        sdk: Any,
        budget: Budget,
        model: str | None = None,
        max_retries: int = 3,
        max_tool_turns: int = 5,
        initial_backoff: float = 1.0,
        sleep: Awaitable[None] | Any = None,
    ) -> None:
        self._sdk = sdk
        self._budget = budget
        self._default_model = model
        self._max_retries = max_retries
        self._max_tool_turns = max_tool_turns
        self._initial_backoff = initial_backoff
        self._sleep = sleep or asyncio.sleep

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        tools: list[ToolSpec] | None = None,
        model: str | None = None,
        cache: bool = False,
        response_format: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> CompletionResult:
        messages = self._build_messages(system, prompt, cache=cache)
        tools_payload = self._build_tools(tools)
        registered = {t.name: t for t in (tools or [])}
        cache_read = 0
        cache_write = 0
        cost = 0.0

        for _turn in range(self._max_tool_turns):
            resp = await self._call_with_retry(
                messages=messages,
                tools=tools_payload,
                model=model or self._default_model,
                response_format=response_format,
                extra_body=extra_body,
            )
            usage = resp.usage
            if usage is not None:
                ptd = getattr(usage, "prompt_tokens_details", None)
                cache_read += getattr(ptd, "cached_tokens", 0) or 0 if ptd else 0
                cache_write += getattr(ptd, "cache_write_tokens", 0) or 0 if ptd else 0
                cost += getattr(usage, "cost", 0.0) or 0.0
            choice = resp.choices[0]
            fr = choice.finish_reason

            if fr == "stop":
                if cache and cache_write == 0:
                    # Cache-warm assertion: one re-warm retry. If still zero, log
                    # CACHE_WARM_FAILED and proceed.
                    retry_resp = await self._call_with_retry(
                        messages=messages,
                        tools=tools_payload,
                        model=model or self._default_model,
                        response_format=response_format,
                        extra_body=extra_body,
                    )
                    retry_usage = retry_resp.usage
                    if retry_usage is not None:
                        ptd = getattr(retry_usage, "prompt_tokens_details", None)
                        cache_read += getattr(ptd, "cached_tokens", 0) or 0 if ptd else 0
                        cache_write += getattr(ptd, "cache_write_tokens", 0) or 0 if ptd else 0
                        cost += getattr(retry_usage, "cost", 0.0) or 0.0
                    retry_choice = retry_resp.choices[0]
                    if retry_choice.finish_reason == "stop":
                        choice = retry_choice
                    if cache_write == 0:
                        self._emit(SpanEvent.CACHE_WARM_FAILED)
                return CompletionResult(
                    text=choice.message.content or "",
                    stop_reason="stop",
                    cache_read_tokens=cache_read,
                    cache_creation_tokens=cache_write,
                    cost_usd=cost,
                )

            if fr == "tool_calls":
                self._assert_tool_allowlist(choice.message.tool_calls, registered)
                messages.append(_assistant_with_tools(choice.message))
                for tc in choice.message.tool_calls:
                    name = _tc_name(tc)
                    args_raw = _tc_arguments(tc)
                    args = json.loads(args_raw)
                    spec = registered[name]
                    spec.args_model.model_validate(args)
                    result = await spec.fn(**args)
                    wrapped = (
                        f'<untrusted_document source="{name}">\n{result}\n</untrusted_document>'
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": _tc_id(tc),
                            "content": wrapped,
                        }
                    )
                continue

            if fr in ("length", "content_filter"):
                msg = f"hard stop: {fr}"
                raise RuntimeError(msg)

            if fr == "error":
                # Belt-and-braces: _call_with_retry is supposed to consume the
                # stream and raise MidStreamError before we ever see this.
                raise MidStreamError(getattr(choice, "error", {"code": "unknown"}))

        msg = "tool-loop bound exceeded"
        raise RuntimeError(msg)

    async def _call_with_retry(self, **kw: Any) -> Any:
        """Call SDK with retry/backoff on transient errors.

        Treats a finish_reason='error' chunk with error.code='overloaded_error'
        as transient by raising MidStreamError, catching it here, and retrying.
        Auth (401/403), 402 (insufficient credits), 503 (no provider), and
        non-overloaded mid-stream errors are fatal — re-raised immediately.
        """
        attempt = 0
        last_exc: BaseException | None = None
        while attempt <= self._max_retries:
            try:
                resp = await self._sdk.chat.completions.create(**kw)
                # Inspect for mid-stream error signal even on a non-streaming-shaped
                # response; the SDK normalizes the final SSE chunk into the same
                # ChatCompletion object whose choices[0].finish_reason='error'
                # carries the upstream error payload.
                if resp.choices and resp.choices[0].finish_reason == "error":
                    err = getattr(resp.choices[0], "error", None) or {"code": "unknown"}
                    raise MidStreamError(err)
                return resp
            except MidStreamError as exc:
                last_exc = exc
                if exc.code not in _TRANSIENT_MIDSTREAM_CODES:
                    raise
                if attempt >= self._max_retries:
                    raise
                await self._backoff(attempt)
                attempt += 1
                continue
            except Exception as exc:
                if not _is_transient_http(exc):
                    raise
                last_exc = exc
                if attempt >= self._max_retries:
                    raise
                await self._backoff(attempt)
                attempt += 1
                continue
        # Unreachable: every loop branch returns or raises. Re-raise as a guard.
        if last_exc is not None:
            raise last_exc
        msg = "retry loop exited without resolution"
        raise RuntimeError(msg)

    async def _backoff(self, attempt: int) -> None:
        delay = self._initial_backoff * (2**attempt)
        delay += random.uniform(0, delay * 0.25)
        await self._sleep(delay)

    def _build_messages(
        self, system: str | None, prompt: str, *, cache: bool
    ) -> list[dict[str, Any]]:
        msgs: list[dict[str, Any]] = []
        if system:
            sys_block: dict[str, Any] = {"type": "text", "text": system}
            if cache:
                sys_block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
            msgs.append({"role": "system", "content": [sys_block]})
        user_block: dict[str, Any] = {"type": "text", "text": prompt}
        if cache:
            user_block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
        msgs.append({"role": "user", "content": [user_block]})
        return msgs

    def _build_tools(self, tools: list[ToolSpec] | None) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        from slopmortem.llm.tools import to_openai_input_schema

        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": to_openai_input_schema(t.args_model),
                },
            }
            for t in tools
        ]

    def _assert_tool_allowlist(self, tcs: Iterable[Any], registered: dict[str, ToolSpec]) -> None:
        for tc in tcs:
            name = _tc_name(tc)
            if name not in registered:
                msg = f"{SpanEvent.TOOL_ALLOWLIST_VIOLATION.value}: {name}"
                raise RuntimeError(msg)

    def _emit(self, event: SpanEvent) -> None:
        # Tracing wiring lands in Task 4. Until then this is a no-op hook the
        # tests can patch to observe emissions.
        return None


def _tc_name(tc: Any) -> str:
    if isinstance(tc, dict):
        return tc["function"]["name"]
    return tc.function.name


def _tc_arguments(tc: Any) -> str:
    if isinstance(tc, dict):
        return tc["function"]["arguments"]
    return tc.function.arguments


def _tc_id(tc: Any) -> str:
    if isinstance(tc, dict):
        return tc["id"]
    return tc.id


def _assistant_with_tools(message: Any) -> dict[str, Any]:
    """Render the assistant turn that requested tool calls back into a message
    payload the next API call can replay.
    """
    tcs = []
    for tc in getattr(message, "tool_calls", []) or []:
        tcs.append(
            {
                "id": _tc_id(tc),
                "type": "function",
                "function": {
                    "name": _tc_name(tc),
                    "arguments": _tc_arguments(tc),
                },
            }
        )
    return {
        "role": "assistant",
        "content": getattr(message, "content", None) or "",
        "tool_calls": tcs,
    }


def _is_transient_http(exc: BaseException) -> bool:
    """Best-effort transient-vs-fatal classification on openai SDK exceptions.

    We check duck-typed attributes so this works for the openai SDK's typed
    exception hierarchy (RateLimitError, APIStatusError, APIConnectionError,
    APITimeoutError, …) without taking a hard import dependency on internals.
    """
    name = type(exc).__name__
    if name in ("APIConnectionError", "APITimeoutError", "RateLimitError"):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        if status in (401, 402, 403, 503):
            return False
        if 500 <= status < 600:
            return True
        if status == 429:
            return True
    return False
