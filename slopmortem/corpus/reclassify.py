"""Re-score quarantined docs; declassify survivors out of the quarantine tree.

Spec ref: §Quarantine and reclassify line 252 — ``slopmortem ingest --reclassify``
re-runs the classifier when the threshold or model changes; declassified docs
flow back toward entity resolution at the next normal ``ingest`` run.

The quarantine row primary key is ``(content_sha256, source, source_id)``
(see :mod:`slopmortem.corpus.merge` schema). Quarantine markdown lives at
``<post_mortems_root>/quarantine/<content_sha256>.md``. Survivors are moved
to ``<post_mortems_root>/raw/<source>/<text_id>.md`` where ``text_id`` is the
first 16 hex chars of the content_sha256 (consistent with
:func:`slopmortem.ingest._text_id_for`'s 16-char shape and what the merge
journal expects). The quarantine row is dropped via
:meth:`MergeJournal.drop_quarantine_row`. The next normal ``ingest`` run
re-fetches the entry from its source and routes it through entity resolution.

Deviation from the originating plan: the plan also called for inserting a
``merge_state="pending"`` row into the main merge journal. The schema's
``canonical_id TEXT NOT NULL`` constraint plus the resolver-flip semantics
in :func:`slopmortem.corpus.entity_resolution.resolve_entity` make this
unsafe without a real ``canonical_id``, which only entity resolution can
assign. We therefore drop the quarantine row and move the file; re-pickup
relies on the next normal ingest re-fetching the entry through its source
adapter and running entity resolution from scratch.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from slopmortem.corpus.paths import safe_path
from slopmortem.models import ReclassifyReport

if TYPE_CHECKING:
    from pathlib import Path

    from slopmortem.corpus.merge import MergeJournal
    from slopmortem.ingest import SlopClassifier

logger = logging.getLogger(__name__)

_TEXT_ID_LEN = 16


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
            :class:`BinocularsSlopClassifier` reflecting the new
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
    total = 0
    declassified = 0
    still_slop = 0
    errors = 0
    for row in rows:
        total += 1
        # ``fetch_quarantined`` returns ``list[dict[str, Any]]`` (sqlite Row →
        # dict at the journal boundary). Narrow each cell to ``object`` for
        # strict-mode typing, then assert ``str`` at the use sites.
        sha = cast("object", row["content_sha256"])
        source = cast("object", row["source"])
        source_id = cast("object", row["source_id"])
        if not (isinstance(sha, str) and isinstance(source, str) and isinstance(source_id, str)):
            errors += 1
            logger.warning("reclassify: non-string key in quarantine row: %r", row)
            continue
        try:
            quarantine_path = safe_path(post_mortems_root, kind="quarantine", content_sha256=sha)
        except ValueError:
            errors += 1
            logger.warning("reclassify: invalid quarantine sha %s", sha)
            continue
        if not quarantine_path.exists():
            errors += 1
            logger.warning("reclassify: missing quarantine file for %s", sha)
            continue
        body = quarantine_path.read_text(encoding="utf-8")
        new_score = await slop_classifier.score(body)
        if new_score >= slop_threshold:
            still_slop += 1
            continue
        # Declassify: derive a text_id from the content_sha256 and move the
        # file under raw/<source>/<text_id>.md. The 16-char shape matches
        # what ``slopmortem.ingest._text_id_for`` produces.
        text_id = sha[:_TEXT_ID_LEN]
        try:
            raw_path = safe_path(post_mortems_root, kind="raw", text_id=text_id, source=source)
        except ValueError:
            errors += 1
            logger.warning("reclassify: cannot build raw path for sha=%s source=%s", sha, source)
            continue
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        quarantine_path.rename(raw_path)
        await journal.drop_quarantine_row(content_sha256=sha, source=source, source_id=source_id)
        declassified += 1
    return ReclassifyReport(
        total=total,
        declassified=declassified,
        still_slop=still_slop,
        errors=errors,
    )
