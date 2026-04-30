from __future__ import annotations

import asyncio

import pytest

from slopmortem.budget import Budget, BudgetExceededError


async def test_reserve_settle_under_gather():
    b = Budget(cap_usd=1.00)

    async def call(reserve_usd: float, actual_usd: float):
        rid = await b.reserve(reserve_usd)
        await asyncio.sleep(0)
        await b.settle(rid, actual_usd)
        return actual_usd

    results = await asyncio.gather(call(0.30, 0.20), call(0.30, 0.20), call(0.30, 0.20))
    assert sum(results) == pytest.approx(0.60)
    assert b.remaining == pytest.approx(0.40)


async def test_exceeded_raises():
    b = Budget(cap_usd=0.10)
    await b.reserve(0.05)
    with pytest.raises(BudgetExceededError):
        await b.reserve(0.10)
