"""Stage test fixtures: re-export the shared Qdrant probe + async client fixture."""

import socket
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from qdrant_client import AsyncQdrantClient


_QDRANT_HOST = "localhost"
_QDRANT_PORT = 6333


def _qdrant_reachable() -> bool:
    try:
        with socket.create_connection((_QDRANT_HOST, _QDRANT_PORT), timeout=0.5):
            return True
    except OSError:
        return False


@pytest_asyncio.fixture
async def qdrant_client() -> AsyncIterator[AsyncQdrantClient]:
    if not _qdrant_reachable():
        pytest.skip("qdrant not reachable on localhost:6333")
    from qdrant_client import AsyncQdrantClient  # noqa: PLC0415

    client = AsyncQdrantClient(host=_QDRANT_HOST, port=_QDRANT_PORT)
    try:
        yield client
    finally:
        await client.close()
