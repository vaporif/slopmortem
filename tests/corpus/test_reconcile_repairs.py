"""Tests for the reconcile repair pass — one minimal test per drift class (a)..(f)."""

import hashlib
from typing import TYPE_CHECKING

from slopmortem.corpus.disk import write_canonical_atomic, write_raw_atomic
from slopmortem.corpus.merge import MergeJournal
from slopmortem.corpus.reconcile import reconcile

if TYPE_CHECKING:
    from pathlib import Path


def _text_id(canonical_id: str) -> str:
    return hashlib.sha256(canonical_id.encode("utf-8")).hexdigest()[:16]


class _MutableCorpus:
    """In-memory corpus stand-in: track which canonicals have chunks and which deletes happened."""

    def __init__(self, present: set[str] | None = None) -> None:
        self._present: set[str] = set(present or set())
        self.upserted: list[str] = []
        self.deleted: list[str] = []

    async def has_chunks(self, canonical_id: str) -> bool:
        return canonical_id in self._present

    async def upsert_chunk(self, point: object) -> None:
        canonical_id_obj = getattr(point, "payload", {}).get("canonical_id")
        if isinstance(canonical_id_obj, str):
            self.upserted.append(canonical_id_obj)
            self._present.add(canonical_id_obj)

    async def delete_chunks_for_canonical(self, canonical_id: str) -> None:
        self.deleted.append(canonical_id)
        self._present.discard(canonical_id)


async def test_repair_class_e_orphan_tmp_files_deleted(tmp_path: Path) -> None:
    base = tmp_path / "post_mortems"
    (base / "canonical").mkdir(parents=True)
    (base / "canonical" / "stale.md.deadbeef.tmp").write_text("partial")
    (base / "raw" / "hn").mkdir(parents=True)
    (base / "raw" / "hn" / "stale.md.deadbeef.tmp").write_text("partial")
    journal = MergeJournal(tmp_path / "j.sqlite")
    await journal.init()
    corpus = _MutableCorpus()

    report = await reconcile(journal, corpus, base, repair=True)

    # Files should be gone; reconcile rows still emitted before deletion.
    e_rows = [r for r in report.rows if r.drift_class == "e"]
    assert len(e_rows) == 2
    assert not (base / "canonical" / "stale.md.deadbeef.tmp").exists()
    assert not (base / "raw" / "hn" / "stale.md.deadbeef.tmp").exists()
    assert any(a == "tmp_deleted" for a in report.applied)


async def test_repair_class_b_pending_marked_for_redo(tmp_path: Path) -> None:
    """Class (b): journal row in pending state; repair re-runs the merge."""
    base = tmp_path / "post_mortems"
    journal = MergeJournal(tmp_path / "j.sqlite")
    await journal.init()
    canonical_id = "b.com"
    text_id = _text_id(canonical_id)
    await write_canonical_atomic(base, text_id, "body", front_matter={"canonical_id": canonical_id})
    await journal.upsert_pending(canonical_id=canonical_id, source="hn", source_id="1")
    corpus = _MutableCorpus(present={canonical_id})

    report = await reconcile(journal, corpus, base, repair=True)

    b_rows = [r for r in report.rows if r.drift_class == "b"]
    assert len(b_rows) == 1
    # Repair for (b) re-merges from raw, which means deleting current chunks.
    # In v1 the repair just records the redo intent — the actual re-merge runs
    # on the next ingest pass against the raw tree. The applied list should
    # mention "pending_redo" so operators can audit.
    assert any(a == "pending_redo" for a in report.applied)


async def test_repair_class_a_canonical_no_chunks_marked(tmp_path: Path) -> None:
    """Class (a): canonical on disk, no Qdrant chunks. Repair flags re-embed needed."""
    base = tmp_path / "post_mortems"
    journal = MergeJournal(tmp_path / "j.sqlite")
    await journal.init()
    canonical_id = "a.com"
    text_id = _text_id(canonical_id)
    await write_canonical_atomic(base, text_id, "body", front_matter={"canonical_id": canonical_id})
    await journal.upsert_pending(canonical_id=canonical_id, source="hn", source_id="1")
    await journal.mark_complete(
        canonical_id=canonical_id,
        source="hn",
        source_id="1",
        skip_key="k",
        merged_at="t",
    )
    corpus = _MutableCorpus(present=set())

    report = await reconcile(journal, corpus, base, repair=True)

    a_rows = [r for r in report.rows if r.drift_class == "a"]
    assert len(a_rows) == 1
    # The repair pass surfaces the re-embed intent; actual embedding runs at the
    # next ingest pass (which has the embed_client + sparse encoder wired up).
    assert any(a == "needs_reembed" for a in report.applied)


async def test_repair_class_c_hash_mismatch_marked(tmp_path: Path) -> None:
    """Class (c): combined_hash mismatch; repair flags re-merge from raw."""
    base = tmp_path / "post_mortems"
    journal = MergeJournal(tmp_path / "j.sqlite")
    await journal.init()
    canonical_id = "c.com"
    text_id = _text_id(canonical_id)
    await write_canonical_atomic(
        base,
        text_id,
        "real body",
        front_matter={"canonical_id": canonical_id, "combined_hash": "deadbeef" * 8},
    )
    await journal.upsert_pending(canonical_id=canonical_id, source="hn", source_id="1")
    await journal.mark_complete(
        canonical_id=canonical_id,
        source="hn",
        source_id="1",
        skip_key="k",
        merged_at="t",
        content_hash="cafebabe" * 8,
    )
    corpus = _MutableCorpus(present={canonical_id})

    report = await reconcile(journal, corpus, base, repair=True)

    c_rows = [r for r in report.rows if r.drift_class == "c"]
    assert len(c_rows) == 1
    assert any(a == "needs_remerge" for a in report.applied)


async def test_repair_class_d_raw_no_journal_marked(tmp_path: Path) -> None:
    """Class (d): raw section without journal row. Repair flags re-merge."""
    base = tmp_path / "post_mortems"
    journal = MergeJournal(tmp_path / "j.sqlite")
    await journal.init()
    text_id = "0123456789abcdef"
    await write_raw_atomic(base, text_id, "hn", "body", front_matter={"canonical_id": "d.com"})
    corpus = _MutableCorpus(present=set())

    report = await reconcile(journal, corpus, base, repair=True)

    d_rows = [r for r in report.rows if r.drift_class == "d"]
    assert len(d_rows) == 1
    assert any(a == "needs_remerge" for a in report.applied)


async def test_repair_class_f_resolver_flipped_strips_prior(tmp_path: Path) -> None:
    """Class (f): resolver_flipped row + prior canonical with stale chunks.

    Repair drops the (source, source_id) from the prior canonical_id (a chunk
    delete is a sufficient v1 action since reconcile cannot re-merge without
    the embed/sparse pipeline). The applied list records ``resolver_flipped_repair``.
    """
    base = tmp_path / "post_mortems"
    journal = MergeJournal(tmp_path / "j.sqlite")
    await journal.init()

    # Prior canonical "newco.com" has chunks.
    prior = "newco.com"
    text_id = _text_id(prior)
    await write_canonical_atomic(base, text_id, "body", front_matter={"canonical_id": prior})

    # Add the resolver_flipped row.
    await journal.upsert_resolver_flipped(canonical_id=prior, source="hn", source_id="9")
    corpus = _MutableCorpus(present={prior})

    report = await reconcile(journal, corpus, base, repair=True)

    f_rows = [r for r in report.rows if r.drift_class == "f"]
    assert len(f_rows) == 1
    assert any(a == "resolver_flipped_repair" for a in report.applied)
