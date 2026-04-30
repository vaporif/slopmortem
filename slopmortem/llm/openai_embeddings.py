# pyright: reportAny=false, reportUnknownMemberType=false, reportUnknownArgumentType=false
"""Async embedding client for OpenAI-compatible APIs that reserves and settles against the budget.

Vendor SDK responses are loosely typed; this file silences `reportAny` and
`reportUnknown*` at the boundary while keeping `reportExplicitAny` per-site.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import TYPE_CHECKING, Any

import anyio
import yaml

from slopmortem.llm.embedding_client import EmbeddingResult
from slopmortem.llm.openrouter import is_transient_http

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from slopmortem.budget import Budget

EMBED_DIMS: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "nomic-ai/nomic-embed-text-v1.5": 768,
}

OPENAI_EMBED_MODELS: frozenset[str] = frozenset(
    {
        "text-embedding-3-small",
        "text-embedding-3-large",
    }
)

_PRICES_PATH = Path(__file__).resolve().parent / "prices.yml"
_PRICES: dict[str, Any] = yaml.safe_load(_PRICES_PATH.read_text())  # pyright: ignore[reportExplicitAny]


def _input_rate_per_million(model: str) -> float:
    key = f"openai/{model}"
    if key not in _PRICES or "input" not in _PRICES[key]:
        msg = (
            f"no input price for {key!r} in prices.yml; "
            f"add an entry under {key!r} with an 'input' rate per 1M tokens"
        )
        raise KeyError(msg)
    return float(_PRICES[key]["input"])


class OpenAIEmbeddingClient:
    """OpenAI-compatible SDK wrapper that embeds text under a shared cost Budget."""

    def __init__(  # noqa: PLR0913 — knobs are public API; users construct this directly.
        self,
        *,
        sdk: object,
        budget: Budget,
        model: str,
        max_retries: int = 3,
        initial_backoff: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        """Bind an SDK instance, budget, and tunable retry knobs."""
        if model not in OPENAI_EMBED_MODELS:
            msg = (
                f"OpenAIEmbeddingClient does not support model {model!r}; "
                f"valid choices: {sorted(OPENAI_EMBED_MODELS)}"
            )
            raise ValueError(msg)
        self._sdk = sdk
        self._budget = budget
        self.model = model
        self._max_retries = max_retries
        self._initial_backoff = initial_backoff
        self._sleep: Callable[[float], Awaitable[None]] = sleep or anyio.sleep

    @property
    def dim(self) -> int:
        """Vector dimensionality for the configured embedding model."""
        return EMBED_DIMS[self.model]

    async def embed(self, texts: list[str], *, model: str | None = None) -> EmbeddingResult:
        """Embed *texts* and settle the real cost against the budget."""
        eff_model = model or self.model
        if not texts:
            return EmbeddingResult(vectors=[], n_tokens=0, cost_usd=0.0)
        rate = _input_rate_per_million(eff_model)
        # Reserve a conservative ceiling assuming ~1k tokens per text in the
        # worst case. The real cost is settled from usage.total_tokens after.
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

    async def _call_with_retry(self, **kw: Any) -> Any:  # pyright: ignore[reportExplicitAny]
        sdk: Any = self._sdk  # pyright: ignore[reportExplicitAny]
        for attempt in range(self._max_retries + 1):
            try:
                return await sdk.embeddings.create(**kw)
            except Exception as exc:
                if not is_transient_http(exc) or attempt >= self._max_retries:
                    raise
                await self._backoff(attempt)
                continue
        msg = "retry loop exited without resolution"  # pragma: no cover — unreachable
        raise RuntimeError(msg)

    async def _backoff(self, attempt: int) -> None:
        delay = self._initial_backoff * (2**attempt)
        delay += random.uniform(0, delay * 0.25)  # noqa: S311 — non-cryptographic jitter
        await self._sleep(delay)
