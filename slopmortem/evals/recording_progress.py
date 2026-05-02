"""Progress Protocol for the eval cassette recorder.

Single phase ``ROWS``: inner ``run_query`` phases collapse onto it as
transient ticks via ``_AggregateProgressBridge``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable


class RecordPhase(StrEnum):
    ROWS = "rows"


@runtime_checkable
class RecordProgress(Protocol):
    """Phase-level progress hooks; ``NullRecordProgress`` decouples the recorder from any UI."""

    def start_phase(self, phase: RecordPhase, total: int | None) -> None: ...

    def advance_phase(self, phase: RecordPhase, n: int = 1) -> None: ...

    def end_phase(self, phase: RecordPhase) -> None: ...

    def set_phase_status(self, phase: RecordPhase, status: str | None) -> None: ...

    def log(self, message: str) -> None: ...

    def error(self, phase: RecordPhase, message: str) -> None: ...


class NullRecordProgress:
    """No-op ``RecordProgress`` for when no display surface is attached."""

    def start_phase(self, phase: RecordPhase, total: int | None) -> None: ...

    def advance_phase(self, phase: RecordPhase, n: int = 1) -> None: ...

    def end_phase(self, phase: RecordPhase) -> None: ...

    def set_phase_status(self, phase: RecordPhase, status: str | None) -> None: ...

    def log(self, message: str) -> None: ...

    def error(self, phase: RecordPhase, message: str) -> None: ...
