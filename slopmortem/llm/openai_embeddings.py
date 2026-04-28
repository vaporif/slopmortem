from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable
from pathlib import Path
from typing import Any

import yaml

from slopmortem.budget import Budget
from slopmortem.llm.embedding_client import EmbeddingResult
from slopmortem.llm.openrouter import _is_transient_http

EMBED_DIMS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
}

_PRICES_PATH = Path(__file__).resolve().parent / "prices.yml"
_PRICES: dict[str, Any] = yaml.safe_load(_PRICES_PATH.read_text())


def _input_rate_per_million(model: str) -> float:
    key = f"openai/{model}"
    if key not in _PRICES or "input" not in _PRICES[key]:
        raise KeyError(
            f"no input price for {key!r} in prices.yml; "
            f"add an entry under {key!r} with an 'input' rate per 1M tokens"
        )
    return float(_PRICES[key]["input"])


class OpenAIEmbeddingClient:
    def __init__(
        self,
        *,
        sdk: Any,
        budget: Budget,
        model: str,
        max_retries: int = 3,
        initial_backoff: float = 1.0,
        sleep: Awaitable[None] | Any = None,
    ):
        if model not in EMBED_DIMS:
            raise ValueError(
                f"unknown embed model {model!r}; add it to EMBED_DIMS"
            )
        self._sdk = sdk
        self._budget = budget
        self.model = model
        self._max_retries = max_retries
        self._initial_backoff = initial_backoff
        self._sleep = sleep or asyncio.sleep

    @property
    def dim(self) -> int:
        return EMBED_DIMS[self.model]

    async def embed(
        self, texts: list[str], *, model: str | None = None
    ) -> EmbeddingResult:
        eff_model = model or self.model
        rate = _input_rate_per_million(eff_model)
        # Reserve a conservative ceiling: assume worst-case ~1k tokens per text.
        # The actual cost is settled after the call from usage.total_tokens.
        ceiling = max(len(texts), 1) * 1000 / 1_000_000 * rate
        rid = await self._budget.reserve(ceiling)
        cost_usd = 0.0
        try:
            resp = await self._call_with_retry(input=texts, model=eff_model)
            vectors = [list(d.embedding) for d in resp.data]
            n_tokens = int(getattr(resp.usage, "total_tokens", 0) or 0)
            cost_usd = n_tokens / 1_000_000 * rate
        finally:
            await self._budget.settle(rid, cost_usd)
        return EmbeddingResult(vectors=vectors, n_tokens=n_tokens, cost_usd=cost_usd)

    async def _call_with_retry(self, **kw: Any) -> Any:
        attempt = 0
        last_exc: BaseException | None = None
        while attempt <= self._max_retries:
            try:
                return await self._sdk.embeddings.create(**kw)
            except Exception as exc:
                if not _is_transient_http(exc):
                    raise
                last_exc = exc
                if attempt >= self._max_retries:
                    raise
                await self._backoff(attempt)
                attempt += 1
                continue
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("retry loop exited without resolution")

    async def _backoff(self, attempt: int) -> None:
        delay = self._initial_backoff * (2**attempt)
        delay += random.uniform(0, delay * 0.25)
        await self._sleep(delay)
