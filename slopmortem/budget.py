from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4


class BudgetExceeded(Exception): ...


@dataclass
class Budget:
    cap_usd: float
    spent_usd: float = 0.0
    reserved: dict[str, float] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def remaining(self) -> float:
        return self.cap_usd - self.spent_usd - sum(self.reserved.values())

    async def reserve(self, amount_usd: float) -> str:
        async with self.lock:
            if self.remaining < amount_usd:
                msg = f"need {amount_usd:.4f}, have {self.remaining:.4f}"
                raise BudgetExceeded(msg)
            rid = uuid4().hex
            self.reserved[rid] = amount_usd
            return rid

    async def settle(self, reservation_id: str, actual_usd: float) -> None:
        async with self.lock:
            self.reserved.pop(reservation_id, None)
            self.spent_usd += actual_usd
