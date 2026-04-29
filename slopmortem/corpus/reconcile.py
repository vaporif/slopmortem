"""``slopmortem ingest --reconcile`` skeleton — walks Qdrant + disk + journal.

Six drift classes per spec line 604:

(a) ``canonical/<text_id>.md`` exists, no Qdrant chunks → re-embed and upsert.
(b) Qdrant point with ``merge_state=pending`` → redo merge.
(c) ``combined_hash`` in canonical front-matter ≠ ``content_hash`` in journal
    → re-merge from raw.
(d) ``raw/<source>/<text_id>.md`` with no journal row, OR canonical missing
    while raw is present → re-merge.
(e) Orphaned ``.tmp`` files in either tree → delete.
(f) Journal row with ``merge_state="resolver_flipped"`` → strip the
    (source, source_id) from the prior canonical, re-route via the normal
    create/merge path under the current canonical_id.

This skeleton scans + reports drift; the actual repair writes land with
Task #5b. Each report row carries enough context for span events.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, Final, Protocol

import yaml
from pydantic import BaseModel

if TYPE_CHECKING:
    from pathlib import Path

    from slopmortem.corpus.merge import MergeJournal


DRIFT_CLASSES: Final[tuple[str, ...]] = ("a", "b", "c", "d", "e", "f")


class _CorpusReadProto(Protocol):
    """Minimal corpus surface reconcile depends on — kept narrow on purpose."""

    async def has_chunks(self, canonical_id: str) -> bool: ...


class ReconcileRow(BaseModel):
    """One drift finding — what file/row, which class, optional repair hint."""

    drift_class: str
    canonical_id: str | None
    source: str | None
    source_id: str | None
    path: str | None
    detail: str


class ReconcileReport(BaseModel):
    """All drift findings produced by a reconcile pass."""

    rows: list[ReconcileRow]


def _text_id(canonical_id: str) -> str:
    return hashlib.sha256(canonical_id.encode("utf-8")).hexdigest()[:16]


def _read_front_matter(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    fm_text = text[4:end]
    parsed = yaml.safe_load(fm_text)  # pyright: ignore[reportAny]  # yaml is loosely typed
    if not isinstance(parsed, dict):
        return {}
    # parsed is dict[Any, Any] from yaml — coerce to dict[str, object] explicitly.
    out: dict[str, object] = {}
    for k, v in parsed.items():  # pyright: ignore[reportUnknownVariableType]
        out[str(k)] = v  # pyright: ignore[reportUnknownArgumentType]
    return out


async def _scan_orphan_tmps(root: Path) -> list[ReconcileRow]:
    def _walk() -> list[Path]:
        if not root.exists():
            return []
        return [p for p in root.rglob("*.tmp") if p.is_file()]

    paths = await asyncio.to_thread(_walk)
    return [
        ReconcileRow(
            drift_class="e",
            canonical_id=None,
            source=None,
            source_id=None,
            path=str(p),
            detail="orphaned .tmp file — delete",
        )
        for p in paths
    ]


async def _scan_canonical_tree(
    journal: MergeJournal,
    corpus: _CorpusReadProto,
    root: Path,
    journal_by_canonical: dict[str, list[dict[str, object]]],
) -> list[ReconcileRow]:
    canonical_dir = root / "canonical"

    def _walk() -> list[Path]:
        return (
            [p for p in canonical_dir.glob("*.md") if p.is_file()] if canonical_dir.exists() else []
        )

    paths = await asyncio.to_thread(_walk)
    rows: list[ReconcileRow] = []
    for p in paths:
        fm = _read_front_matter(p)
        canonical_id = fm.get("canonical_id")
        if not isinstance(canonical_id, str):
            continue
        # (a) canonical on disk, no qdrant chunks.
        if not await corpus.has_chunks(canonical_id):
            rows.append(
                ReconcileRow(
                    drift_class="a",
                    canonical_id=canonical_id,
                    source=None,
                    source_id=None,
                    path=str(p),
                    detail="canonical on disk has no qdrant chunks",
                )
            )
        # (c) combined_hash mismatch with journal content_hash.
        combined_hash = fm.get("combined_hash")
        journal_rows = journal_by_canonical.get(canonical_id, [])
        if combined_hash and journal_rows:
            journal_hash = next(
                (r.get("content_hash") for r in journal_rows if r.get("content_hash")),
                None,
            )
            if journal_hash and journal_hash != combined_hash:
                rows.append(
                    ReconcileRow(
                        drift_class="c",
                        canonical_id=canonical_id,
                        source=None,
                        source_id=None,
                        path=str(p),
                        detail=(
                            f"combined_hash {combined_hash!r} on disk "
                            f"≠ journal content_hash {journal_hash!r}"
                        ),
                    )
                )
    _ = journal  # journal is unused here but kept on the signature for symmetry
    return rows


def _classify_raw_row(  # noqa: PLR0913 — local helper, all args carry classification state
    p: Path,
    source: str,
    fm: dict[str, object],
    canonical_id: str | None,
    *,
    has_journal_row: bool,
    canonical_present: bool,
) -> ReconcileRow | None:
    """Return a class-(d) row for *p*, or None if no drift detected."""
    src_id = str(fm.get("source_id")) if fm.get("source_id") else None
    if canonical_id is None:
        return ReconcileRow(
            drift_class="d",
            canonical_id=None,
            source=source,
            source_id=None,
            path=str(p),
            detail="raw section missing canonical_id front-matter",
        )
    if not has_journal_row:
        return ReconcileRow(
            drift_class="d",
            canonical_id=canonical_id,
            source=source,
            source_id=src_id,
            path=str(p),
            detail="raw section has no journal row",
        )
    if not canonical_present:
        return ReconcileRow(
            drift_class="d",
            canonical_id=canonical_id,
            source=source,
            source_id=src_id,
            path=str(p),
            detail="raw present but canonical missing",
        )
    return None


async def _scan_raw_tree(
    root: Path,
    journal_by_canonical: dict[str, list[dict[str, object]]],
) -> list[ReconcileRow]:
    raw_dir = root / "raw"

    def _walk() -> list[tuple[Path, str]]:
        if not raw_dir.exists():
            return []
        out: list[tuple[Path, str]] = []
        for source_dir in raw_dir.iterdir():
            if not source_dir.is_dir():
                continue
            out.extend((p, source_dir.name) for p in source_dir.glob("*.md") if p.is_file())
        return out

    raw_files = await asyncio.to_thread(_walk)
    rows: list[ReconcileRow] = []
    for p, source in raw_files:
        fm = _read_front_matter(p)
        canonical_raw = fm.get("canonical_id")
        canonical_id = canonical_raw if isinstance(canonical_raw, str) else None
        canonical_present = (
            canonical_id is not None
            and (root / "canonical" / f"{_text_id(canonical_id)}.md").exists()
        )
        finding = _classify_raw_row(
            p,
            source,
            fm,
            canonical_id,
            has_journal_row=canonical_id in journal_by_canonical,
            canonical_present=canonical_present,
        )
        if finding is not None:
            rows.append(finding)
    return rows


def _scan_journal_states(
    rows_by_canonical: dict[str, list[dict[str, object]]],
) -> list[ReconcileRow]:
    out: list[ReconcileRow] = []
    for canonical_id, rows in rows_by_canonical.items():
        for r in rows:
            state = r.get("merge_state")
            if state == "pending":
                out.append(
                    ReconcileRow(
                        drift_class="b",
                        canonical_id=canonical_id,
                        source=str(r.get("source")) if r.get("source") else None,
                        source_id=str(r.get("source_id")) if r.get("source_id") else None,
                        path=None,
                        detail="journal row in pending state — redo merge",
                    )
                )
            elif state == "resolver_flipped":
                out.append(
                    ReconcileRow(
                        drift_class="f",
                        canonical_id=canonical_id,
                        source=str(r.get("source")) if r.get("source") else None,
                        source_id=str(r.get("source_id")) if r.get("source_id") else None,
                        path=None,
                        detail="resolver flipped — re-route under current canonical_id",
                    )
                )
    return out


async def reconcile(
    journal: MergeJournal,
    corpus: _CorpusReadProto,
    post_mortems_root: Path,
) -> ReconcileReport:
    """Walk Qdrant + disk + journal and emit a :class:`ReconcileReport`."""
    all_rows = await journal.fetch_all()
    by_canonical: dict[str, list[dict[str, object]]] = {}
    for r in all_rows:
        # r is dict[str, Any] from sqlite; coerce to dict[str, object] for the
        # downstream scanners which only call .get() on it.
        coerced: dict[str, object] = dict(r)
        canonical_id = str(coerced["canonical_id"])
        by_canonical.setdefault(canonical_id, []).append(coerced)

    findings: list[ReconcileRow] = []
    findings.extend(_scan_journal_states(by_canonical))
    findings.extend(await _scan_canonical_tree(journal, corpus, post_mortems_root, by_canonical))
    findings.extend(await _scan_raw_tree(post_mortems_root, by_canonical))
    findings.extend(await _scan_orphan_tmps(post_mortems_root))
    return ReconcileReport(rows=findings)
