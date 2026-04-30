# pyright: reportAny=false
"""SQLite-backed merge journal, quarantine table, and alias graph.

The async surface routes every sqlite call through
:func:`anyio.to_thread.run_sync`. One short-lived connection per call, no
pool. Every connection uses WAL and ``busy_timeout=5000``.

Terminal-state writers (atomicity contract, spec line 538):

- :meth:`MergeJournal.upsert_pending`
- :meth:`MergeJournal.upsert_resolver_flipped`
- :meth:`MergeJournal.upsert_alias_blocked`

Each runs its inserts inside one ``BEGIN; ... COMMIT;`` so a crash either
commits everything or nothing. ``mark_complete`` is the only path from
``pending`` to ``complete``, and runs after the qdrant and disk writes
succeed.

Quarantine rows live in their own table keyed on
``(content_sha256, source, source_id)``. They have no ``canonical_id`` or
``merge_state`` column; quarantined docs do not live in the main journal.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from anyio import to_thread

from slopmortem._time import utcnow_iso
from slopmortem.models import AliasEdge, PendingReviewRow

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS merge_journal (
        canonical_id TEXT NOT NULL,
        source       TEXT NOT NULL,
        source_id    TEXT NOT NULL,
        merge_state  TEXT NOT NULL,
        skip_key     TEXT,
        content_hash TEXT,
        merged_at    TEXT,
        PRIMARY KEY (canonical_id, source, source_id)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS merge_reverse_idx
      ON merge_journal(source, source_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS quarantine_journal (
        content_sha256  TEXT NOT NULL,
        source          TEXT NOT NULL,
        source_id       TEXT NOT NULL,
        reason          TEXT NOT NULL,
        slop_score      REAL,
        quarantined_at  TEXT NOT NULL,
        PRIMARY KEY (content_sha256, source, source_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS aliases (
        canonical_id        TEXT NOT NULL,
        alias_kind          TEXT NOT NULL,
        target_canonical_id TEXT NOT NULL,
        evidence_source_id  TEXT NOT NULL,
        confidence          REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pending_review (
        pair_key          TEXT PRIMARY KEY,
        similarity_score  REAL,
        haiku_decision    TEXT,
        haiku_rationale   TEXT,
        raw_section_heads TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS founding_year_cache (
        registrable_domain TEXT NOT NULL,
        content_sha256     TEXT NOT NULL,
        founding_year      INTEGER,
        PRIMARY KEY (registrable_domain, content_sha256)
    )
    """,
)


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a short-lived connection with WAL and a 5s busy timeout."""
    conn = sqlite3.connect(db_path, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:  # pyright: ignore[reportExplicitAny]
    return {k: row[k] for k in row.keys()}  # noqa: SIM118 — sqlite3.Row needs .keys()


class MergeJournal:
    """Async wrapper over a SQLite merge journal. See module docstring."""

    def __init__(self, db_path: Path) -> None:
        """Bind the journal to a sqlite file path; ``init()`` creates the schema."""
        self._db = db_path

    async def init(self) -> None:
        """Create tables and indexes if missing."""
        await to_thread.run_sync(self._init_sync)

    def _init_sync(self) -> None:
        self._db.parent.mkdir(parents=True, exist_ok=True)
        with _connect(self._db) as conn:
            for stmt in _SCHEMA:
                conn.execute(stmt)

    # ─── Terminal-state writers (atomic) ────────────────────────────────────

    async def upsert_pending(
        self,
        *,
        canonical_id: str,
        source: str,
        source_id: str,
    ) -> None:
        """Mark a row pending in a single-row transaction."""
        await to_thread.run_sync(self._upsert_state, canonical_id, source, source_id, "pending")

    async def upsert_resolver_flipped(
        self,
        *,
        canonical_id: str,
        source: str,
        source_id: str,
    ) -> None:
        """Mark a row resolver_flipped in a single-row transaction."""
        await to_thread.run_sync(
            self._upsert_state, canonical_id, source, source_id, "resolver_flipped"
        )

    async def upsert_alias_blocked(
        self,
        *,
        canonical_id: str,
        source: str,
        source_id: str,
        alias_edge: AliasEdge,
    ) -> None:
        """Atomically write a journal alias_blocked row and an alias graph edge.

        Both inserts run inside one ``BEGIN; ... COMMIT;`` (spec line 538).
        """
        await to_thread.run_sync(
            self._upsert_alias_blocked_sync,
            canonical_id,
            source,
            source_id,
            alias_edge,
        )

    def _upsert_state(self, canonical_id: str, source: str, source_id: str, state: str) -> None:
        with _connect(self._db) as conn:
            conn.execute("BEGIN")
            try:
                # Resolver flip: this (source, source_id) is now bound to a
                # new canonical_id, and the UNIQUE reverse index would block
                # the insert otherwise. Drop the prior row first when the
                # new state is 'resolver_flipped'. Prior canonical's chunks,
                # raw, and canonical files stay on disk; reconcile drift
                # class (f) handles repair.
                if state == "resolver_flipped":
                    conn.execute(
                        """
                        DELETE FROM merge_journal
                         WHERE source = ? AND source_id = ?
                           AND canonical_id <> ?
                        """,
                        (source, source_id, canonical_id),
                    )
                conn.execute(
                    """
                    INSERT INTO merge_journal
                      (canonical_id, source, source_id, merge_state)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(canonical_id, source, source_id) DO UPDATE SET
                      merge_state = excluded.merge_state
                    """,
                    (canonical_id, source, source_id, state),
                )
            except Exception:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    def _upsert_alias_blocked_sync(
        self,
        canonical_id: str,
        source: str,
        source_id: str,
        alias_edge: AliasEdge,
    ) -> None:
        with _connect(self._db) as conn:
            conn.execute("BEGIN")
            try:
                conn.execute(
                    """
                    INSERT INTO merge_journal
                      (canonical_id, source, source_id, merge_state)
                    VALUES (?, ?, ?, 'alias_blocked')
                    ON CONFLICT(canonical_id, source, source_id) DO UPDATE SET
                      merge_state = 'alias_blocked'
                    """,
                    (canonical_id, source, source_id),
                )
                conn.execute(
                    """
                    INSERT INTO aliases
                      (canonical_id, alias_kind, target_canonical_id,
                       evidence_source_id, confidence)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        alias_edge.canonical_id,
                        alias_edge.alias_kind,
                        alias_edge.target_canonical_id,
                        alias_edge.evidence_source_id,
                        alias_edge.confidence,
                    ),
                )
            except Exception:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    # ─── Promotion path ─────────────────────────────────────────────────────

    async def mark_complete(  # noqa: PLR0913 — keyword-only journal write contract
        self,
        *,
        canonical_id: str,
        source: str,
        source_id: str,
        skip_key: str,
        merged_at: str,
        content_hash: str | None = None,
    ) -> None:
        """Promote a pending row to ``merge_state='complete'``, writing skip_key last."""
        await to_thread.run_sync(
            self._mark_complete_sync,
            canonical_id,
            source,
            source_id,
            skip_key,
            merged_at,
            content_hash,
        )

    def _mark_complete_sync(  # noqa: PLR0913 — signature mirrors public method
        self,
        canonical_id: str,
        source: str,
        source_id: str,
        skip_key: str,
        merged_at: str,
        content_hash: str | None,
    ) -> None:
        with _connect(self._db) as conn:
            conn.execute(
                """
                UPDATE merge_journal
                   SET merge_state = 'complete',
                       skip_key    = ?,
                       merged_at   = ?,
                       content_hash = COALESCE(?, content_hash)
                 WHERE canonical_id = ? AND source = ? AND source_id = ?
                """,
                (skip_key, merged_at, content_hash, canonical_id, source, source_id),
            )

    # ─── Reads ──────────────────────────────────────────────────────────────

    async def fetch_pending(self) -> list[dict[str, Any]]:  # pyright: ignore[reportExplicitAny]
        """Return every row currently in ``merge_state='pending'`` as a dict."""
        return await to_thread.run_sync(self._fetch_pending_sync)

    def _fetch_pending_sync(self) -> list[dict[str, Any]]:  # pyright: ignore[reportExplicitAny]
        with _connect(self._db) as conn:
            cur = conn.execute("SELECT * FROM merge_journal WHERE merge_state = 'pending'")
            return [_row_to_dict(r) for r in cur.fetchall()]

    async def fetch_by_key(
        self, canonical_id: str, source: str, source_id: str
    ) -> list[dict[str, Any]]:  # pyright: ignore[reportExplicitAny]
        """Return the row(s) matching the full primary key. Always 0 or 1 entries."""
        return await to_thread.run_sync(self._fetch_by_key_sync, canonical_id, source, source_id)

    def _fetch_by_key_sync(
        self, canonical_id: str, source: str, source_id: str
    ) -> list[dict[str, Any]]:  # pyright: ignore[reportExplicitAny]
        with _connect(self._db) as conn:
            cur = conn.execute(
                """
                SELECT * FROM merge_journal
                 WHERE canonical_id = ? AND source = ? AND source_id = ?
                """,
                (canonical_id, source, source_id),
            )
            return [_row_to_dict(r) for r in cur.fetchall()]

    async def lookup_canonical_for_source(self, source: str, source_id: str) -> str | None:
        """Reverse-index lookup: the prior canonical_id for (source, source_id)."""
        return await to_thread.run_sync(self._lookup_reverse_sync, source, source_id)

    def _lookup_reverse_sync(self, source: str, source_id: str) -> str | None:
        with _connect(self._db) as conn:
            cur = conn.execute(
                """
                SELECT canonical_id FROM merge_journal
                 WHERE source = ? AND source_id = ?
                 ORDER BY CASE merge_state
                            WHEN 'complete' THEN 0
                            WHEN 'pending' THEN 1
                            ELSE 2
                          END
                 LIMIT 1
                """,
                (source, source_id),
            )
            row = cur.fetchone()
            return None if row is None else str(row["canonical_id"])

    async def fetch_aliases(self, canonical_id: str) -> list[AliasEdge]:
        """Return every alias edge that has ``canonical_id`` as the source."""
        rows = await to_thread.run_sync(self._fetch_aliases_sync, canonical_id)
        return [AliasEdge.model_validate(r) for r in rows]

    def _fetch_aliases_sync(self, canonical_id: str) -> list[dict[str, Any]]:  # pyright: ignore[reportExplicitAny]
        with _connect(self._db) as conn:
            cur = conn.execute(
                """
                SELECT canonical_id, alias_kind, target_canonical_id,
                       evidence_source_id, confidence
                  FROM aliases
                 WHERE canonical_id = ?
                """,
                (canonical_id,),
            )
            return [_row_to_dict(r) for r in cur.fetchall()]

    async def fetch_all(self) -> list[dict[str, Any]]:  # pyright: ignore[reportExplicitAny]
        """Return every merge_journal row. Used by reconcile."""
        return await to_thread.run_sync(self._fetch_all_sync)

    def _fetch_all_sync(self) -> list[dict[str, Any]]:  # pyright: ignore[reportExplicitAny]
        with _connect(self._db) as conn:
            cur = conn.execute("SELECT * FROM merge_journal")
            return [_row_to_dict(r) for r in cur.fetchall()]

    # ─── Quarantine: separate table, no canonical_id, no merge_state ──────

    async def write_quarantine(
        self,
        *,
        content_sha256: str,
        source: str,
        source_id: str,
        reason: str,
        slop_score: float | None,
    ) -> None:
        """Record a slop-classified quarantine row. No canonical_id is assigned."""
        await to_thread.run_sync(
            self._write_quarantine_sync,
            content_sha256,
            source,
            source_id,
            reason,
            slop_score,
        )

    def _write_quarantine_sync(
        self,
        content_sha256: str,
        source: str,
        source_id: str,
        reason: str,
        slop_score: float | None,
    ) -> None:
        with _connect(self._db) as conn:
            conn.execute(
                """
                INSERT INTO quarantine_journal
                  (content_sha256, source, source_id, reason, slop_score, quarantined_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(content_sha256, source, source_id) DO UPDATE SET
                  reason = excluded.reason,
                  slop_score = excluded.slop_score
                """,
                (content_sha256, source, source_id, reason, slop_score, utcnow_iso()),
            )

    async def fetch_quarantined(self) -> list[dict[str, Any]]:  # pyright: ignore[reportExplicitAny]
        """Return every quarantine row as a dict. The column set has no merge_state."""
        return await to_thread.run_sync(self._fetch_quarantined_sync)

    def _fetch_quarantined_sync(self) -> list[dict[str, Any]]:  # pyright: ignore[reportExplicitAny]
        with _connect(self._db) as conn:
            cur = conn.execute("SELECT * FROM quarantine_journal")
            return [_row_to_dict(r) for r in cur.fetchall()]

    async def drop_quarantine_row(
        self,
        *,
        content_sha256: str,
        source: str,
        source_id: str,
    ) -> None:
        """Delete the quarantine_journal row for the given primary key.

        Used by ``slopmortem ingest --reclassify`` after a doc is declassified
        (re-scored below ``slop_threshold``) and its markdown is moved out of
        the quarantine tree.
        """
        await to_thread.run_sync(self._drop_quarantine_row_sync, content_sha256, source, source_id)

    def _drop_quarantine_row_sync(self, content_sha256: str, source: str, source_id: str) -> None:
        with _connect(self._db) as conn:
            conn.execute(
                """
                DELETE FROM quarantine_journal
                 WHERE content_sha256 = ? AND source = ? AND source_id = ?
                """,
                (content_sha256, source, source_id),
            )

    # ─── Pending review queue (entity-resolution borderline pairs) ─────────

    async def list_pending_review(self) -> list[PendingReviewRow]:
        """Read all rows from the ``pending_review`` table (spec line 264).

        Returns rows in INSERT order (no explicit ``ORDER BY`` — ``--list-review``
        is exploratory; the caller can sort if it cares about ordering).
        """
        return await to_thread.run_sync(self._list_pending_review_sync)

    def _list_pending_review_sync(self) -> list[PendingReviewRow]:
        with _connect(self._db) as conn:
            cur = conn.execute("SELECT * FROM pending_review")
            return [
                PendingReviewRow(
                    pair_key=row["pair_key"],
                    similarity_score=row["similarity_score"],
                    haiku_decision=row["haiku_decision"],
                    haiku_rationale=row["haiku_rationale"],
                    raw_section_heads=row["raw_section_heads"],
                )
                for row in cur.fetchall()
            ]


# Used by ingest in later tasks. Exported here for symmetry with the other writers.
def aliases_iterable(edges: Iterable[AliasEdge]) -> list[dict[str, Any]]:  # pyright: ignore[reportExplicitAny]
    """Render a list of :class:`AliasEdge` to plain dicts (for journaling, etc.)."""
    return [e.model_dump() for e in edges]
