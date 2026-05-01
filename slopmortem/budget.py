"""Per-pipeline USD budget. Concurrent-safe reserve/settle bookkeeping."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4

import anyio


class BudgetExceededError(Exception):
    """Raised when reserve cannot accommodate or settle pushes spent over cap."""


@dataclass
class Budget:
    """Coroutine-safe USD cap shared across every LLM and embedding call in a pipeline."""

    cap_usd: float
    spent_usd: float = 0.0
    reserved: dict[str, float] = field(default_factory=dict)
    lock: anyio.Lock = field(default_factory=anyio.Lock)

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
        """Drop the reservation, credit *actual_usd*, raise if spent exceeds cap.

        Raising after the fact still bounds total spend: the call that pushed over
        is paid for, but no further call gets past the pre-call gate or a future
        reserve(). Concurrent fan-out can briefly run with multiple in-flight
        calls past the cap; this is documented and accepted.
        """
        async with self.lock:
            self.reserved.pop(reservation_id, None)
            self.spent_usd += actual_usd
            if self.spent_usd > self.cap_usd:
                msg = f"spent {self.spent_usd:.4f} > cap {self.cap_usd:.4f}"
                raise BudgetExceededError(msg)
