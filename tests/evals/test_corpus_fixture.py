"""Round-trip tests for corpus_fixture dump/restore and SHA stability."""

from __future__ import annotations

import os
import socket
import uuid
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import pytest
import pytest_asyncio

from slopmortem.evals.corpus_fixture import (
    compute_fixture_sha256,
    dump_collection_to_jsonl,
    restore_jsonl_to_collection,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from qdrant_client import AsyncQdrantClient

pytestmark = pytest.mark.requires_qdrant


_DEFAULT_QDRANT_URL = "http://localhost:6333"


def _qdrant_reachable(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 6333
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.fixture
def qdrant_url() -> str:
    return os.environ.get("QDRANT_URL", _DEFAULT_QDRANT_URL)


@pytest_asyncio.fixture
async def client(qdrant_url: str) -> AsyncIterator[AsyncQdrantClient]:
    if not _qdrant_reachable(qdrant_url):
        pytest.skip(f"qdrant not reachable at {qdrant_url}")
    from qdrant_client import AsyncQdrantClient  # noqa: PLC0415 — avoid hard import on skip

    c = AsyncQdrantClient(url=qdrant_url)
    try:
        yield c
    finally:
        await c.close()


@pytest.fixture
def collection_name() -> str:
    return f"slopmortem_test_{os.getpid()}_{uuid.uuid4().hex}"


async def test_round_trip_preserves_query_results(
    client: AsyncQdrantClient, collection_name: str, tmp_path: Path
) -> None:
    from slopmortem.corpus import ensure_collection  # noqa: PLC0415

    await ensure_collection(client, collection_name, dim=8)
    fixture_path = tmp_path / "out.jsonl"
    await dump_collection_to_jsonl(client, collection_name, fixture_path)
    assert fixture_path.exists()
    sha_a = compute_fixture_sha256(fixture_path)
    assert len(sha_a) == 64

    fresh = collection_name + "_fresh"
    await ensure_collection(client, fresh, dim=8)
    try:
        await restore_jsonl_to_collection(client, fresh, fixture_path)
        out_b = tmp_path / "out_b.jsonl"
        await dump_collection_to_jsonl(client, fresh, out_b)
        assert sorted(fixture_path.read_text().splitlines()) == sorted(
            out_b.read_text().splitlines()
        )
    finally:
        await client.delete_collection(fresh)
        await client.delete_collection(collection_name)


def test_sha256_changes_when_content_changes(tmp_path: Path) -> None:
    p = tmp_path / "f.jsonl"
    p.write_text('{"a": 1}\n')
    a = compute_fixture_sha256(p)
    p.write_text('{"a": 2}\n')
    b = compute_fixture_sha256(p)
    assert a != b


def test_sha256_stable_across_calls(tmp_path: Path) -> None:
    p = tmp_path / "f.jsonl"
    p.write_text('{"a": 1}\n')
    assert compute_fixture_sha256(p) == compute_fixture_sha256(p)
