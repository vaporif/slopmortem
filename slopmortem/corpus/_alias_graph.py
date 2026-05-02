"""Union-find over alias edges to dedupe canonicals belonging to one lifecycle.

M&A, rebrand, or pivot leaves two canonicals for one lifecycle; the ``aliases``
table records the lineage edges. Pure Python, no I/O — callers pass edges in.
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
    """*candidates* must arrive in descending-score order — the head of each component becomes the representative."""
    if not candidates:
        return []
    parents: dict[str, str] = {c.canonical_id: c.canonical_id for c in candidates}
    cand_ids = set(parents)

    for edge in edges:
        a, b = edge.canonical_id, edge.target_canonical_id
        # An edge to a canonical that wasn't retrieved has nothing to dedupe against.
        if a in cand_ids and b in cand_ids:
            _union(parents, a, b)

    by_root: dict[str, list[Candidate]] = {}
    for c in candidates:
        by_root.setdefault(_find(parents, c.canonical_id), []).append(c)

    out: list[Candidate] = []
    for group in by_root.values():
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
