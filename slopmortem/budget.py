"""Per-pipeline USD budget. Concurrent-safe reserve/settle bookkeeping."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4


class BudgetExceededError(Exception):
    """Raised when ``Budget.reserve`` cannot accommodate a requested amount."""


@dataclass
class Budget:
    """An asyncio-safe USD cap shared across every LLM and embedding call in a pipeline."""

    cap_usd: float
    spent_usd: float = 0.0
    reserved: dict[str, float] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def remaining(self) -> float:
        """USD left after subtracting settled spend and outstanding reservations."""
        return self.cap_usd - self.spent_usd - sum(self.reserved.values())

    async def reserve(self, amount_usd: float) -> str:
        """Reserve *amount_usd* under the lock; return a reservation id for settle()."""
        async with self.lock:
            if self.remaining < amount_usd:
                msg = f"need {amount_usd:.4f}, have {self.remaining:.4f}"
                raise BudgetExceededError(msg)
            rid = uuid4().hex
            self.reserved[rid] = amount_usd
            return rid

    async def settle(self, reservation_id: str, actual_usd: float) -> None:
        """Drop the reservation and credit *actual_usd* against ``spent_usd``."""
        async with self.lock:
            self.reserved.pop(reservation_id, None)
            self.spent_usd += actual_usd
