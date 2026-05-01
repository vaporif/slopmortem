# pyright: reportAny=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportIndexIssue=false, reportOptionalSubscript=false
"""Qdrant-backed corpus store: collection bootstrap, read methods, and chunk upsert.

Vendor-SDK boundary module. Qdrant's models are loosely typed (``Optional``
/ ``Mapping`` everywhere), so the per-file ``reportAny`` / ``reportUnknown*``
silences match the pattern from ``slopmortem/llm/openai_embeddings.py``.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from qdrant_client.models import (
    Distance,
    Modifier,
    SparseIndexParams,
    SparseVectorParams,
    VectorParams,
)

from slopmortem.corpus.alias_graph import collapse_alias_components
from slopmortem.corpus.disk import read_canonical
from slopmortem.corpus.paths import safe_path
from slopmortem.models import Candidate, CandidatePayload

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from qdrant_client import AsyncQdrantClient

    from slopmortem.models import AliasEdge, Facets


async def ensure_collection(client: AsyncQdrantClient, name: str, *, dim: int) -> None:
    """Create a hybrid (dense + sparse) collection or verify existing dim matches.

    Args:
        client: An :class:`AsyncQdrantClient` connected to the running service.
        name: Collection name.
        dim: Required dense vector dimensionality. Read by callers from
            :data:`slopmortem.llm.openai_embeddings.EMBED_DIMS` keyed on
            ``settings.embed_model_id`` (the single source of truth).

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
    """Live :class:`Corpus` impl backed by a Qdrant service plus on-disk markdown tree.

    Vectors and small payload live in Qdrant. The full canonical body lives
    on disk under ``<post_mortems_root>/canonical/<text_id>.md`` and loads on
    demand via ``get_post_mortem``.
    """

    def __init__(  # noqa: PLR0913 — orchestration knobs are public construction surface
        self,
        *,
        client: AsyncQdrantClient,
        collection: str,
        post_mortems_root: Path,
        facet_boost: float = 0.01,
        rrf_k: int = 60,
        fetch_aliases: Callable[[str], Awaitable[list[AliasEdge]]] | None = None,
    ) -> None:
        """Bind a Qdrant client, collection name, and on-disk markdown root.

        Args:
            client: Live :class:`AsyncQdrantClient`.
            collection: Qdrant collection name.
            post_mortems_root: Root for ``raw/`` and ``canonical/`` markdown.
            facet_boost: Per-non-``"other"`` facet match boost added to the
                inner RRF score. ``0.01`` matches the spec's provisional
                value (lifts ~0.04 max for a 4-facet match against an RRF
                ceiling of ~0.033).
            rrf_k: Reciprocal-rank-fusion ``k`` constant, passed through to
                the inner :class:`Prefetch`. Default 60 matches Qdrant's
                server default.
            fetch_aliases: Optional async fetcher (canonical_id -> alias
                edges). When ``None``, the alias-graph dedup pass is a no-op
                (handy for tests that don't seed an aliases table).
        """
        self._client = client
        self._collection = collection
        self._root = post_mortems_root
        self._facet_boost = facet_boost
        self._rrf_k = rrf_k
        self._fetch_aliases = fetch_aliases

    async def query(  # noqa: PLR0913 — Protocol method signature is the public contract
        self,
        *,
        dense: list[float],
        sparse: dict[int, float],
        facets: Facets,
        cutoff_iso: str | None,
        strict_deaths: bool,
        k_retrieve: int,
    ) -> list[Candidate]:
        """Hybrid retrieve top-K candidates with FormulaQuery facet boost.

        Inner :class:`Prefetch` with dense+sparse RRF fusion, outer
        :class:`FormulaQuery` adding a per-facet boost (skipping ``"other"``)
        on top of ``$score``, and a recency :class:`Filter` with three branches
        (or one under ``--strict-deaths``).

        Over-fetches chunks at ``k_retrieve * 4`` so the in-Python collapse
        to parents has room. After collapse,
        :func:`collapse_alias_components` dedupes alias-graph connected
        components and the result truncates to ``k_retrieve``.

        Args:
            dense: Dense query vector.
            sparse: Sparse query vector as ``{token_id: weight}``.
            facets: Facets to soft-boost on; ``"other"`` values skipped.
            cutoff_iso: ISO-8601 lower bound for the recency filter, or
                ``None`` to disable filtering.
            strict_deaths: When ``True``, recency requires a known
                ``failure_date >= cutoff_iso`` (branch A only).
            k_retrieve: Final number of parent candidates to return.

        Returns:
            Up to ``k_retrieve`` :class:`Candidate` objects in descending
            score order, deduped by alias-graph component.
        """
        from qdrant_client.models import (  # noqa: PLC0415 — keep top-level imports lean
            FieldCondition,
            Filter,
            FormulaQuery,
            Fusion,
            FusionQuery,
            MatchValue,
            MultExpression,
            Prefetch,
            SparseVector,
            SumExpression,
        )

        # Inner prefetch: dense and sparse, fused by RRF.
        inner = Prefetch(
            prefetch=[
                Prefetch(query=dense, using="dense", limit=k_retrieve * 2),
                Prefetch(
                    query=SparseVector(
                        indices=list(sparse),
                        values=list(sparse.values()),
                    ),
                    using="sparse",
                    limit=k_retrieve * 2,
                ),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=k_retrieve * 2,
        )

        # Build the facet-boost FilterCondition. Skip "other" deliberately.
        # Free-form fields (sub_sector, product_type, ...) and year integers
        # stay out of the soft-boost set. Only closed taxonomy fields participate.
        boost_must: list[Any] = []  # pyright: ignore[reportExplicitAny]
        for fname in ("sector", "business_model", "customer_type", "geography", "monetization"):
            val = getattr(facets, fname)
            if val == "other":
                continue
            boost_must.append(FieldCondition(key=f"facets.{fname}", match=MatchValue(value=val)))

        # Outer formula: $score + boost * Filter(must=boost_must). When
        # boost_must is empty the Filter matches every doc (a 1.0
        # contribution on every candidate). Drop the Mult term in
        # that case so "no facet boost in play" stays neutral.
        formula_terms: list[Any] = ["$score"]  # pyright: ignore[reportExplicitAny]
        if boost_must:
            formula_terms.append(MultExpression(mult=[self._facet_boost, Filter(must=boost_must)]))
        formula = FormulaQuery(formula=SumExpression(sum=formula_terms))

        query_filter = _build_recency_filter(
            cutoff_iso=cutoff_iso,
            strict_deaths=strict_deaths,
        )

        # TODO(prod): chunk over-fetch may under-fill long post-mortems (#25).
        # ``k_retrieve * 4`` assumes ~4 chunks per parent on average. Long
        # post-mortems can chunk into many more, silently under-filling the
        # parent set after collapse. Measure ``len(best)`` vs ``k_retrieve``
        # against a real corpus and bump the multiplier (or switch to a
        # parent-aware fetcher) before going live.
        resp = await self._client.query_points(
            collection_name=self._collection,
            prefetch=inner,
            query=formula,
            query_filter=query_filter,
            limit=k_retrieve * 4,
            with_payload=True,
            with_vectors=False,
        )

        # Collapse chunk hits to parents: best-score per canonical_id.
        # ``query_points`` returns ``QueryResponse`` whose ``.points`` is a
        # list of ``ScoredPoint``.
        best: dict[str, tuple[float, dict[str, Any]]] = {}  # pyright: ignore[reportExplicitAny]
        for hit in resp.points:
            payload = dict(hit.payload or {})
            cid = str(payload.get("canonical_id", ""))
            if not cid:
                continue
            score = float(hit.score)
            if cid not in best or score > best[cid][0]:
                best[cid] = (score, payload)

        # Build Candidates in descending score order. Bad payloads are
        # per-doc isolated (logged and dropped) so one malformed point can't
        # fail the whole query.
        # TODO(perf): cache pydantic-validated payloads if hot (#26).
        # Keyed on (canonical_id, chunk_idx); payloads are immutable
        # post-ingest so the cache is safe. Skip until measurements justify it.
        ordered = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)
        candidates: list[Candidate] = []
        for cid, (score, payload) in ordered:
            try:
                cp = _payload_dict_to_candidate_payload(payload)
            except Exception as exc:  # noqa: BLE001 — per-doc isolation; we log and continue
                logger.warning("qdrant_query: dropped malformed payload for %r: %s", cid, exc)
                continue
            candidates.append(Candidate(canonical_id=cid, score=score, payload=cp))

        # Alias-graph dedup. The fetcher is per-canonical_id; gather every
        # edge referencing any retrieved canonical.
        # Known limitation: only fetches edges for canonicals in the top-K.
        # Alias chains longer than 1 hop where a mid-chain node was pruned
        # upstream stay un-collapsed. See README "Known limitations".
        # A transitive-closure pass would fix it if this shows up in practice.
        if self._fetch_aliases is not None and candidates:
            import asyncio  # noqa: PLC0415 — local to keep top-level imports lean

            edge_lists = await asyncio.gather(
                *(self._fetch_aliases(c.canonical_id) for c in candidates)
            )
            edges: list[AliasEdge] = [e for sub in edge_lists for e in sub]
            candidates = collapse_alias_components(candidates, edges)

        return candidates[:k_retrieve]

    async def get_post_mortem(self, canonical_id: str) -> str:
        """Read the canonical merged markdown body for *canonical_id*."""
        text_id = hashlib.sha256(canonical_id.encode("utf-8")).hexdigest()[:16]
        return read_canonical(self._root, text_id)

    async def search_corpus(
        self,
        q: str,
        facets: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:  # pyright: ignore[reportExplicitAny]  # Protocol surface
        """Scroll-based search; returns a list of payload dicts.

        v1 uses Qdrant's payload scroll filtering on canonical_id and the
        provided facet keys. Full text relevance arrives with the FormulaQuery
        path in Task #7. The synthesis-tool layer asks for a handful of hits
        at most.
        """
        from qdrant_client.models import (  # noqa: PLC0415 — keep import surface lean
            FieldCondition,
            Filter,
            MatchText,
            MatchValue,
        )

        must: list[Any] = [  # pyright: ignore[reportExplicitAny]
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
        out: list[dict[str, Any]] = []  # pyright: ignore[reportExplicitAny]
        for rec in records:
            payload = dict(rec.payload or {})
            payload["_point_id"] = rec.id
            out.append(payload)
        return out

    async def upsert_chunk(self, point: Any) -> None:  # pyright: ignore[reportExplicitAny]
        """Upsert a single chunk point into the collection (used by ingest).

        Ingest hands in a :class:`slopmortem.ingest._Point` (a dataclass with
        ``id``, ``vector={"dense": list[float], "sparse": dict[int, float]}``,
        ``payload``). qdrant-client's ``PointsList`` is strict pydantic v2 and
        rejects arbitrary dataclasses, so build a real ``PointStruct`` here.
        """
        from qdrant_client.models import PointStruct, SparseVector  # noqa: PLC0415

        sparse = point.vector["sparse"]
        struct = PointStruct(
            id=point.id,
            vector={
                "dense": point.vector["dense"],
                "sparse": SparseVector(
                    indices=list(sparse),
                    values=list(sparse.values()),
                ),
            },
            payload=point.payload,
        )
        await self._client.upsert(
            collection_name=self._collection,
            points=[struct],
        )


def canonical_path_for(post_mortems_root: Path, canonical_id: str) -> Path:
    """Return the validated on-disk path to the canonical doc for *canonical_id*."""
    text_id = hashlib.sha256(canonical_id.encode("utf-8")).hexdigest()[:16]
    return safe_path(post_mortems_root, kind="canonical", text_id=text_id)


def _build_recency_filter(*, cutoff_iso: str | None, strict_deaths: bool) -> Any:  # pyright: ignore[reportExplicitAny]
    """Compose the three-branch recency :class:`Filter` (single-branch in strict mode).

    Branches A/B/C use derived ``failure_date_unknown`` /
    ``founding_date_unknown`` boolean payloads instead of ``IsNullCondition``
    (qdrant#5148, documented slow under indexed payloads).
    """
    if cutoff_iso is None:
        return None

    from qdrant_client.models import (  # noqa: PLC0415
        DatetimeRange,
        FieldCondition,
        Filter,
        MatchValue,
    )

    # ``DatetimeRange.gte`` is typed ``datetime | date | None``. Runtime
    # accepts ISO strings via pydantic coercion, but the stub is strict, so
    # parse once here to keep the rest narrow. Python 3.11+ ``fromisoformat``
    # handles the trailing ``Z`` directly.
    cutoff_dt = datetime.fromisoformat(cutoff_iso)

    branch_a_must: list[Any] = [  # pyright: ignore[reportExplicitAny]
        FieldCondition(key="failure_date_unknown", match=MatchValue(value=False)),
        FieldCondition(key="failure_date", range=DatetimeRange(gte=cutoff_dt)),
    ]
    if strict_deaths:
        return Filter(must=branch_a_must)

    branch_b_must: list[Any] = [  # pyright: ignore[reportExplicitAny]
        FieldCondition(key="failure_date_unknown", match=MatchValue(value=True)),
        FieldCondition(key="founding_date_unknown", match=MatchValue(value=False)),
        FieldCondition(key="founding_date", range=DatetimeRange(gte=cutoff_dt)),
    ]
    branch_c_must: list[Any] = [  # pyright: ignore[reportExplicitAny]
        FieldCondition(key="failure_date_unknown", match=MatchValue(value=True)),
        FieldCondition(key="founding_date_unknown", match=MatchValue(value=True)),
    ]
    return Filter(
        should=[
            Filter(must=branch_a_must),
            Filter(must=branch_b_must),
            Filter(must=branch_c_must),
        ]
    )


def _payload_dict_to_candidate_payload(payload: dict[str, Any]) -> CandidatePayload:  # pyright: ignore[reportExplicitAny]
    """Validate a Qdrant payload dict back into a :class:`CandidatePayload`.

    Pydantic v2 handles ISO-8601 strings to ``date`` via ``model_validate``.
    Qdrant-only keys (``canonical_id``, ``chunk_idx``) get dropped before
    validation so they don't trigger ``extra="forbid"``. The model isn't
    strict today, but the cleanup keeps payloads small.
    """
    cleaned = {k: v for k, v in payload.items() if k not in ("canonical_id", "chunk_idx")}
    return CandidatePayload.model_validate(cleaned)
