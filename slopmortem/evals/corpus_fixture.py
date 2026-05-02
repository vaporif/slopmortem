# pyright: reportAny=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportExplicitAny=false, reportAttributeAccessIssue=false
"""JSONL dump/restore + SHA for the eval corpus fixture (regenerable via ``just eval-record-corpus``).

Per-file pyright silences mirror ``slopmortem/corpus/qdrant_store.py``:
SDK-boundary code, Qdrant types are loose.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from anyio import to_thread

if TYPE_CHECKING:
    from pathlib import Path

    from qdrant_client import AsyncQdrantClient


_SCROLL_LIMIT = 256
_UPSERT_BATCH = 64
_SHA_CHUNK = 64 * 1024


def compute_fixture_sha256(path: Path) -> str:
    """Return the sha256 hex digest of ``path``."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_SHA_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


async def dump_collection_to_jsonl(
    client: AsyncQdrantClient, collection: str, out_path: Path
) -> None:
    """Scroll ``collection`` and write one JSON object per line, sorted by ``canonical_id`` for stable diffs."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    offset: Any = None
    rows: list[dict[str, object]] = []
    while True:
        points, next_offset = await client.scroll(
            collection_name=collection,
            limit=_SCROLL_LIMIT,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        for p in points:
            payload = dict(p.payload or {})
            vectors = p.vector or {}
            dense = vectors.get("dense") if isinstance(vectors, dict) else None
            sparse = vectors.get("sparse") if isinstance(vectors, dict) else None
            sparse_indices = list(sparse.indices) if sparse is not None else []
            sparse_values = list(sparse.values) if sparse is not None else []
            rows.append(
                {
                    "canonical_id": payload.get("canonical_id"),
                    "dense": list(dense) if dense is not None else [],
                    "sparse_indices": sparse_indices,
                    "sparse_values": sparse_values,
                    "payload": payload,
                }
            )
        if next_offset is None:
            break
        offset = next_offset

    rows.sort(key=lambda r: str(r.get("canonical_id") or ""))
    payload = "\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n"
    await to_thread.run_sync(out_path.write_text, payload)


async def restore_jsonl_to_collection(
    client: AsyncQdrantClient, collection: str, jsonl_path: Path
) -> None:
    """Bulk-upsert every line of ``jsonl_path`` into ``collection`` (must already exist with the right vector config)."""
    from qdrant_client.models import PointStruct, SparseVector  # noqa: PLC0415

    points: list[PointStruct] = []
    with jsonl_path.open() as f:
        for point_id, raw_line in enumerate(f):
            line = raw_line.strip()
            if not line:
                continue
            data = json.loads(line)
            sparse = SparseVector(
                indices=list(data.get("sparse_indices") or []),
                values=list(data.get("sparse_values") or []),
            )
            point = PointStruct(
                id=point_id,
                vector={"dense": list(data["dense"]), "sparse": sparse},
                payload=data.get("payload") or {},
            )
            points.append(point)
            if len(points) >= _UPSERT_BATCH:
                await client.upsert(collection_name=collection, points=points, wait=True)
                points = []
    if points:
        await client.upsert(collection_name=collection, points=points, wait=True)
