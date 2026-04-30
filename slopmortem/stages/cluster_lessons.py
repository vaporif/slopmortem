"""Pure cross-candidate clustering of synthesized lessons by token-set similarity.

Rule-based, deterministic, fully synchronous: zero network calls, zero new
dependencies. Greedy single-pass clustering using Jaccard similarity over
normalized bag-of-words token sets.

The 0.5 threshold is the v1 number: higher values (0.7) reject paraphrases
like "segregate customer assets" vs "customer assets must be segregated"
that we want merged; lower values (0.3) over-merge unrelated lessons that
share generic words. Bumped to LLM-call dedup is a follow-up task, not this
one. Keep this module pure and synchronous.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from slopmortem.models import TopRisk, TopRisks

if TYPE_CHECKING:
    from slopmortem.models import Synthesis

# Threshold for adding a lesson to an existing cluster. See module docstring
# for the reasoning behind 0.5.
_JACCARD_THRESHOLD = 0.5

# Tiny stop-word set. Aggressive removal would degrade clustering on
# already-short lesson text — keep this list minimal.
_STOP_WORDS: frozenset[str] = frozenset(
    {"a", "an", "the", "of", "to", "for", "in", "on", "and", "or", "with", "by"}
)

_PUNCT_RE = re.compile(r"[^\w\s]")


def _normalize(text: str) -> frozenset[str]:
    """Lowercase, strip punctuation, drop stop-words, return token frozenset."""
    lowered = text.lower()
    cleaned = _PUNCT_RE.sub(" ", lowered)
    tokens = cleaned.split()
    return frozenset(t for t in tokens if t and t not in _STOP_WORDS)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity ``|A & B| / |A | B|``; 0.0 when both empty."""
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


class _Cluster:
    """Mutable scratch cluster used during the greedy pass.

    Centroid is the union of every member's token set — converges fast enough
    in practice and avoids the cost of recomputing a true centroid (mean) on
    every insert. Members carry the raw text plus its token set so we can pick
    the canonical summary at the end without re-normalizing.
    """

    __slots__ = ("candidate_ids", "centroid", "members")

    def __init__(self, *, candidate_id: str, text: str, tokens: frozenset[str]) -> None:
        self.centroid: frozenset[str] = tokens
        self.candidate_ids: list[str] = [candidate_id]
        # ``members`` retains insertion order; we walk it to choose the summary.
        self.members: list[tuple[str, frozenset[str]]] = [(text, tokens)]

    def add(self, *, candidate_id: str, text: str, tokens: frozenset[str]) -> None:
        """Add a lesson to this cluster, deduping candidate ids in encounter order."""
        self.centroid = self.centroid | tokens
        self.members.append((text, tokens))
        if candidate_id not in self.candidate_ids:
            self.candidate_ids.append(candidate_id)

    def summary(self) -> str:
        """Shortest member's original text; ties broken by encounter order."""
        # ``min`` with a stable key returns the first match on ties, which gives
        # us encounter-order tiebreaking for free.
        return min(self.members, key=lambda m: len(m[0]))[0]


def cluster_lessons(syntheses: list[Synthesis]) -> TopRisks:
    """Cluster lessons across syntheses by token-set similarity.

    Greedy single-pass clustering: each lesson is added to the existing
    cluster with the highest centroid Jaccard similarity if that similarity
    is at least :data:`_JACCARD_THRESHOLD`; otherwise the lesson seeds a
    new cluster. Lessons that normalize to an empty token set always seed
    their own cluster (never merged).

    Args:
        syntheses: Successful per-candidate :class:`Synthesis` results from
            the synth stage. Empty list yields an empty :class:`TopRisks`.

    Returns:
        :class:`TopRisks` with clusters sorted by ``frequency`` descending,
        ties broken by ``summary`` ascending for stable, deterministic output.
        Each cluster's ``summary`` is the shortest contributing lesson's
        original text; ``candidate_ids`` is deduped in encounter order;
        ``frequency`` is ``len(candidate_ids)``.
    """
    if not syntheses:
        return TopRisks(clusters=[])

    clusters: list[_Cluster] = []
    # Dedup identical lesson text from the same candidate so a single source
    # can't inflate its own cluster's frequency by repetition.
    seen_per_candidate: set[tuple[str, str]] = set()

    for syn in syntheses:
        for lesson in syn.lessons_for_input:
            key = (syn.candidate_id, lesson.strip().lower())
            if key in seen_per_candidate:
                continue
            seen_per_candidate.add(key)

            tokens = _normalize(lesson)

            # Empty-token lessons (pure punctuation, all stop-words) get their
            # own cluster — never merged, since Jaccard against anything is
            # either 0 or undefined.
            if not tokens:
                clusters.append(_Cluster(candidate_id=syn.candidate_id, text=lesson, tokens=tokens))
                continue

            best_idx: int | None = None
            best_score = 0.0
            for idx, cluster in enumerate(clusters):
                if not cluster.centroid:
                    continue
                score = _jaccard(tokens, cluster.centroid)
                if score > best_score:
                    best_score = score
                    best_idx = idx

            if best_idx is not None and best_score >= _JACCARD_THRESHOLD:
                clusters[best_idx].add(candidate_id=syn.candidate_id, text=lesson, tokens=tokens)
            else:
                clusters.append(_Cluster(candidate_id=syn.candidate_id, text=lesson, tokens=tokens))

    top_risks = [
        TopRisk(
            summary=c.summary(),
            candidate_ids=list(c.candidate_ids),
            frequency=len(c.candidate_ids),
        )
        for c in clusters
    ]
    # Sort frequency desc, then summary asc for stable output.
    top_risks.sort(key=lambda r: (-r.frequency, r.summary))
    return TopRisks(clusters=top_risks)
