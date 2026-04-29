# pyright: reportAny=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportIndexIssue=false, reportOptionalSubscript=false
"""Qdrant-backed corpus store: collection bootstrap + read methods + chunk upsert.

The full ``query()`` impl with ``FormulaQuery`` lands in Task #7 — the read
helpers ``get_post_mortem`` (canonical/<text_id>.md) and ``search_corpus``
(scroll/text scan over payloads) are needed earlier by the synthesis-tool layer.

Vendor-SDK boundary module: Qdrant's models are loosely typed (``Optional`` /
``Mapping`` everywhere), so per-file ``reportAny`` / ``reportUnknown*`` silences
match the pattern used by ``slopmortem/llm/openai_embeddings.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from qdrant_client.models import (
    Distance,
    Modifier,
    SparseIndexParams,
    SparseVectorParams,
    VectorParams,
)

from slopmortem.corpus.disk import read_canonical
from slopmortem.corpus.paths import safe_path

if TYPE_CHECKING:
    from pathlib import Path

    from qdrant_client import AsyncQdrantClient

    from slopmortem.models import Candidate, Facets


async def ensure_collection(client: AsyncQdrantClient, name: str, *, dim: int) -> None:
    """Create a hybrid (dense + sparse) collection or verify existing dim matches.

    Args:
        client: An :class:`AsyncQdrantClient` connected to the running service.
        name: Collection name.
        dim: Required dense vector dimensionality. Read by callers from
            :data:`slopmortem.llm.openai_embeddings.EMBED_DIMS` keyed on
            ``settings.embed_model_id`` — the single source of truth.

    Raises:
        ValueError: If the collection already exists with a different dim.
    """
    if await client.collection_exists(name):
        info = await client.get_collection(name)
        existing = info.config.params.vectors["dense"].size
        if existing != dim:
            msg = (
                f"dim mismatch: collection {name!r} has dim={existing} but config "
                f"wants dim={dim}. Drop data/qdrant/ and re-ingest."
            )
            raise ValueError(msg)
        return
    await client.create_collection(
        collection_name=name,
        vectors_config={"dense": VectorParams(size=dim, distance=Distance.COSINE)},
        sparse_vectors_config={
            "sparse": SparseVectorParams(
                index=SparseIndexParams(on_disk=False),
                modifier=Modifier.IDF,
            )
        },
    )


class QdrantCorpus:
    """Live :class:`Corpus` impl backed by a Qdrant service + on-disk markdown tree.

    Holds vectors and small payload in Qdrant; the full canonical body lives on
    disk under ``<post_mortems_root>/canonical/<text_id>.md`` and is loaded on
    demand by ``get_post_mortem``.
    """

    def __init__(
        self,
        *,
        client: AsyncQdrantClient,
        collection: str,
        post_mortems_root: Path,
    ) -> None:
        """Bind a Qdrant client + collection name + on-disk markdown root."""
        self._client = client
        self._collection = collection
        self._root = post_mortems_root

    async def query(  # noqa: PLR0913 — Protocol method signature is the public contract
        self,
        *,
        dense: list[float],
        sparse: dict[int, float],
        facets: Facets,
        years_filter: int | None,
        strict_deaths: bool,
        k_retrieve: int,
    ) -> list[Candidate]:
        """Hybrid retrieve top-K candidates — full impl in Task #7."""
        _ = (dense, sparse, facets, years_filter, strict_deaths, k_retrieve)
        msg = "Task 7"
        raise NotImplementedError(msg)

    async def get_post_mortem(self, canonical_id: str) -> str:
        """Read the canonical merged markdown body for *canonical_id*."""
        import hashlib  # noqa: PLC0415 — keep top-level imports lean

        text_id = hashlib.sha256(canonical_id.encode("utf-8")).hexdigest()[:16]
        return read_canonical(self._root, text_id)

    async def search_corpus(
        self,
        q: str,
        facets: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:  # type: ignore[explicit-any]  # Protocol surface
        """Lightweight scroll-based search; returns a list of payload dicts.

        v1 uses Qdrant's payload scroll filtering on the canonical_id +
        provided facet keys; full text relevance arrives with the FormulaQuery
        path in Task #7. The synthesis-tool layer asks for at most a handful
        of hits.
        """
        from qdrant_client.models import (  # noqa: PLC0415 — keep import surface lean
            FieldCondition,
            Filter,
            MatchText,
            MatchValue,
        )

        must: list[Any] = [  # type: ignore[explicit-any]
            FieldCondition(key="body", match=MatchText(text=q)),
        ]
        if facets:
            must.extend(
                FieldCondition(key=f"facets.{key}", match=MatchValue(value=value))
                for key, value in facets.items()
            )
        flt = Filter(must=must)
        records, _next = await self._client.scroll(
            collection_name=self._collection,
            scroll_filter=flt,
            limit=10,
            with_payload=True,
            with_vectors=False,
        )
        out: list[dict[str, Any]] = []  # type: ignore[explicit-any]
        for rec in records:
            payload = dict(rec.payload or {})
            payload["_point_id"] = rec.id
            out.append(payload)
        return out

    async def upsert_chunk(self, point: Any) -> None:  # type: ignore[explicit-any]
        """Upsert a single chunk point into the collection — used by ingest."""
        await self._client.upsert(
            collection_name=self._collection,
            points=[point],
        )


def canonical_path_for(post_mortems_root: Path, canonical_id: str) -> Path:
    """Return the validated on-disk path to the canonical doc for *canonical_id*."""
    import hashlib  # noqa: PLC0415

    text_id = hashlib.sha256(canonical_id.encode("utf-8")).hexdigest()[:16]
    return safe_path(post_mortems_root, kind="canonical", text_id=text_id)
