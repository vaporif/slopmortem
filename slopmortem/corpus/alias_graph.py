"""Union-find over alias edges to dedupe canonicals belonging to one lifecycle.

Retrieve emits parent-collapsed candidates (one per ``canonical_id``), but an
M&A, rebrand, or pivot can leave two canonicals for what's really one
lifecycle. The ``aliases`` SQLite table (see :mod:`slopmortem.corpus.merge`)
records those lineage edges. This helper groups candidates into connected
components and returns the top-scoring representative per component, with the
other canonicals stashed on its ``alias_canonicals`` list.

Pure Python, no I/O. Callers fetch alias edges however they like (per-id
calls to :meth:`MergeJournal.fetch_aliases` or a one-shot scan of the
``aliases`` table) and pass them in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from slopmortem.models import AliasEdge, Candidate


def _find(parents: dict[str, str], node: str) -> str:
    # Iterative path-halving for stack safety on long chains.
    while parents.get(node, node) != node:
        nxt = parents[node]
        parents[node] = parents.get(nxt, nxt)
        node = parents[node]
    return node


def _union(parents: dict[str, str], a: str, b: str) -> None:
    ra, rb = _find(parents, a), _find(parents, b)
    if ra != rb:
        parents[ra] = rb


def collapse_alias_components(
    candidates: list[Candidate],
    edges: Iterable[AliasEdge],
) -> list[Candidate]:
    """Collapse alias-connected candidates to one representative per component.

    Args:
        candidates: Parent-collapsed candidates in retrieval-score order
            (highest first). Each candidate's ``score`` is the best chunk
            score for its parent.
        edges: Every :class:`AliasEdge` whose ``canonical_id`` OR
            ``target_canonical_id`` appears in *candidates*. Edges referring
            to canonicals not in *candidates* are no-ops.

    Returns:
        One :class:`Candidate` per connected component, in descending score
        order. The representative is the highest-scoring candidate in its
        component. The other canonicals in the component are stored on the
        representative's ``alias_canonicals`` list (preserving any prior
        contents).
    """
    if not candidates:
        return []
    parents: dict[str, str] = {c.canonical_id: c.canonical_id for c in candidates}
    cand_ids = set(parents)

    for edge in edges:
        a, b = edge.canonical_id, edge.target_canonical_id
        # Only union when both endpoints are in the result set. An edge to a
        # canonical that wasn't retrieved has nothing to dedupe against.
        if a in cand_ids and b in cand_ids:
            _union(parents, a, b)

    # Group by root.
    by_root: dict[str, list[Candidate]] = {}
    for c in candidates:
        by_root.setdefault(_find(parents, c.canonical_id), []).append(c)

    out: list[Candidate] = []
    for group in by_root.values():
        # Input order is descending score, so the head is the rep.
        rep = group[0]
        if len(group) == 1:
            out.append(rep)
            continue
        others = [g.canonical_id for g in group[1:]]
        existing = list(rep.alias_canonicals)
        for cid in others:
            if cid not in existing:
                existing.append(cid)
        out.append(rep.model_copy(update={"alias_canonicals": existing}))

    out.sort(key=lambda c: c.score, reverse=True)
    return out
