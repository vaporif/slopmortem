"""Re-score quarantined docs; declassify survivors out of the quarantine tree.

``slopmortem ingest --reclassify`` re-runs the classifier when the threshold
or model changes; declassified docs flow back toward entity resolution on the
next normal ``ingest`` run.

The quarantine row primary key is ``(content_sha256, source, source_id)``
(see :mod:`slopmortem.corpus.merge` schema). Quarantine markdown lives at
``<post_mortems_root>/quarantine/<content_sha256>.md``. Survivors move to
``<post_mortems_root>/raw/<source>/<text_id>.md``, where ``text_id`` is the
first 16 hex chars of the content_sha256 (consistent with
:func:`slopmortem.ingest._text_id_for` and what the merge journal expects).
The quarantine row is dropped via
:meth:`MergeJournal.drop_quarantine_row`. The next normal ``ingest`` run
re-fetches the entry from its source and routes it through entity resolution.

Deviation from the originating plan: the plan also called for inserting a
``merge_state="pending"`` row into the main merge journal. The schema's
``canonical_id TEXT NOT NULL`` constraint plus the resolver-flip semantics
in :func:`slopmortem.corpus.entity_resolution.resolve_entity` make this unsafe
without a real ``canonical_id``, which only entity resolution can assign. So
we drop the quarantine row and move the file. Re-pickup relies on the next
normal ingest re-fetching the entry through its source adapter and running
entity resolution from scratch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from slopmortem.concurrency import gather_resilient
from slopmortem.corpus.paths import safe_path
from slopmortem.models import ReclassifyReport

if TYPE_CHECKING:
    from pathlib import Path

    from slopmortem.corpus.merge import MergeJournal
    from slopmortem.ingest import SlopClassifier

logger = logging.getLogger(__name__)

_TEXT_ID_LEN = 16


@dataclass
class _Pending:
    """A quarantine row whose body has been read and is ready to score."""

    sha: str
    source: str
    source_id: str
    quarantine_path: Path
    body: str


def _row_to_pending(row: dict[str, object], post_mortems_root: Path) -> _Pending | None:
    """Validate one ``quarantine_journal`` row and read its body; ``None`` on any failure."""
    # ``fetch_quarantined`` returns sqlite-Row-shaped dicts at the journal
    # boundary; assert ``str`` on each key cell at this use site.
    sha = row["content_sha256"]
    source = row["source"]
    source_id = row["source_id"]
    if not (isinstance(sha, str) and isinstance(source, str) and isinstance(source_id, str)):
        logger.warning("reclassify: non-string key in quarantine row: %r", row)
        return None
    try:
        quarantine_path = safe_path(post_mortems_root, kind="quarantine", content_sha256=sha)
    except ValueError:
        logger.warning("reclassify: invalid quarantine sha %s", sha)
        return None
    try:
        body = quarantine_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("reclassify: missing quarantine file for %s", sha)
        return None
    return _Pending(
        sha=sha,
        source=source,
        source_id=source_id,
        quarantine_path=quarantine_path,
        body=body,
    )


async def _score_all(
    pending: list[_Pending], slop_classifier: SlopClassifier
) -> list[float | BaseException]:
    """Score every pending body.

    The first call is awaited in isolation before fan-out so any one-time
    setup in the classifier (cache warm, HTTP connection pool, lazy model
    load) happens once instead of in N racing copies.
    """
    if not pending:
        return []
    scores: list[float | BaseException] = []
    try:
        scores.append(await slop_classifier.score(pending[0].body))
    except Exception as exc:  # noqa: BLE001 — defensive: per-row isolation.
        scores.append(exc)
    scores.extend(await gather_resilient(*(slop_classifier.score(p.body) for p in pending[1:])))
    return scores


async def reclassify_quarantined(
    *,
    journal: MergeJournal,
    slop_classifier: SlopClassifier,
    post_mortems_root: Path,
    slop_threshold: float,
) -> ReclassifyReport:
    """Re-run the slop classifier against every row in ``quarantine_journal``.

    Survivors (new score < ``slop_threshold``) are removed from the
    quarantine journal and their markdown files are moved into the raw
    tree at ``raw/<source>/<text_id>.md``. Docs that still score above
    threshold stay in quarantine. Missing markdown files (or an invalid
    quarantine path) increment ``errors`` and the loop continues.

    Args:
        journal: The merge journal whose ``quarantine_journal`` table we
            iterate (via :meth:`MergeJournal.fetch_quarantined`).
        slop_classifier: The current classifier; usually a fresh
            :class:`HaikuSlopClassifier` reflecting the new
            threshold or model id.
        post_mortems_root: Root containing ``raw/``, ``canonical/``,
            ``quarantine/`` subtrees.
        slop_threshold: Strict-less-than threshold; scores below it
            declassify.

    Returns:
        A :class:`ReclassifyReport` with ``total``, ``declassified``,
        ``still_slop``, and ``errors`` counts.
    """
    rows = await journal.fetch_quarantined()
    total = len(rows)
    pending: list[_Pending] = []
    for row in rows:
        p = _row_to_pending(row, post_mortems_root)
        if p is not None:
            pending.append(p)
    errors = total - len(pending)

    scores = await _score_all(pending, slop_classifier)

    declassified = 0
    still_slop = 0
    for p, score in zip(pending, scores, strict=True):
        if isinstance(score, BaseException):
            errors += 1
            logger.warning("reclassify: classifier failed for %s: %s", p.sha, score)
            continue
        if score >= slop_threshold:
            still_slop += 1
            continue
        # Declassify: derive a text_id from the content_sha256 and move the
        # file under raw/<source>/<text_id>.md. The 16-char shape matches
        # what ``slopmortem.ingest._text_id_for`` produces.
        text_id = p.sha[:_TEXT_ID_LEN]
        try:
            raw_path = safe_path(post_mortems_root, kind="raw", text_id=text_id, source=p.source)
        except ValueError:
            errors += 1
            logger.warning(
                "reclassify: cannot build raw path for sha=%s source=%s", p.sha, p.source
            )
            continue
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        p.quarantine_path.rename(raw_path)
        await journal.drop_quarantine_row(
            content_sha256=p.sha, source=p.source, source_id=p.source_id
        )
        declassified += 1
    return ReclassifyReport(
        total=total,
        declassified=declassified,
        still_slop=still_slop,
        errors=errors,
    )
