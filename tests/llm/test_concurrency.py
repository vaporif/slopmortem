from __future__ import annotations

import asyncio

from slopmortem.llm import gather_with_limit


async def test_capacity_limiter_caps_inflight():
    inflight = 0
    peak = 0
    lock = asyncio.Lock()

    async def task(i: int) -> int:
        nonlocal inflight, peak
        async with lock:
            inflight += 1
            peak = max(peak, inflight)
        await asyncio.sleep(0.01)
        async with lock:
            inflight -= 1
        return i

    results = await gather_with_limit([task(i) for i in range(20)], limit=4)
    assert sorted(r for r in results if isinstance(r, int)) == list(range(20))
    assert peak <= 4


async def test_returns_exceptions_does_not_short_circuit():
    async def good(i: int) -> int:
        return i

    async def bad() -> int:
        msg = "nope"
        raise RuntimeError(msg)

    results = await gather_with_limit(
        [good(1), bad(), good(2)],
        limit=2,
    )
    # Three results, in submission order; the middle one is the exception.
    assert results[0] == 1
    assert isinstance(results[1], RuntimeError)
    assert results[2] == 2
