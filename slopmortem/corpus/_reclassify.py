"""Re-score quarantined docs; declassify survivors out of the quarantine tree.

Survivors move from ``quarantine/<sha>.md`` to ``raw/<source>/<text_id>.md``
(text_id = first 16 hex of content_sha256, matching ``ingest._text_id_for``);
the quarantine row is dropped.

No ``merge_state="pending"`` row is written here — the schema's NOT NULL
canonical_id constraint plus resolver-flip semantics need a real canonical_id,
which only entity resolution can assign. The next ingest re-fetches and
resolves from scratch.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from slopmortem.concurrency import gather_resilient
from slopmortem.corpus._paths import safe_path
from slopmortem.models import ReclassifyReport

if TYPE_CHECKING:
    from pathlib import Path

    from slopmortem.corpus._merge import MergeJournal
    from slopmortem.ingest import SlopClassifier

logger = logging.getLogger(__name__)

_TEXT_ID_LEN = 16


@dataclass
class _Pending:
    sha: str
    source: str
    source_id: str
    quarantine_path: Path
    body: str


def _row_to_pending(row: dict[str, object], post_mortems_root: Path) -> _Pending | None:
    """Validate one ``quarantine_journal`` row and read its body; ``None`` on any failure."""
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
    """First call awaited alone so cache warm / lazy model load doesn't race the fan-out."""
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
    """Survivors (``< slop_threshold``) leave quarantine; missing/invalid files bump ``errors``."""
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
        # text_id matches ``ingest._text_id_for`` so the next ingest pass sees
        # a consistent path layout.
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
