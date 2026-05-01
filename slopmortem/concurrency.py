"""Concurrency helpers that don't fit anyio's task-group cancellation model.

anyio's :class:`anyio.abc.TaskGroup` cancels every sibling on the first
exception. That's the right default for most of the codebase, but the fan-out
sites in :mod:`slopmortem.ingest` and :mod:`slopmortem.stages.synthesize` need
the opposite — one failed candidate must not cancel its siblings.
:func:`gather_resilient` wraps the asyncio primitive that gives that
behaviour, so the rest of the code only has to import this helper.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable


async def gather_resilient[T](*aws: Awaitable[T]) -> list[T | BaseException]:
    """Run *aws* concurrently; per-task exceptions are returned, never raised."""
    return await asyncio.gather(*aws, return_exceptions=True)
