from __future__ import annotations

import hashlib
import json

from slopmortem.corpus.disk import write_canonical_atomic, write_raw_atomic
from slopmortem.corpus.merge import MergeJournal
from slopmortem.corpus.reconcile import DRIFT_CLASSES, reconcile


def _text_id(canonical_id: str) -> str:
    return hashlib.sha256(canonical_id.encode("utf-8")).hexdigest()[:16]


class FakeCorpus:
    """Minimal corpus stand-in: only ``has_chunks`` is consulted by reconcile."""

    def __init__(self, present_canonicals: set[str]) -> None:
        self._present = present_canonicals

    async def has_chunks(self, canonical_id: str) -> bool:
        return canonical_id in self._present


def test_drift_classes_enumerated():
    # Plan / spec line 604: six classes (a)..(f).
    assert set(DRIFT_CLASSES) == {"a", "b", "c", "d", "e", "f"}


async def test_reconcile_class_a_canonical_no_qdrant(tmp_path):
    base = tmp_path / "post_mortems"
    journal = MergeJournal(tmp_path / "j.sqlite")
    await journal.init()
    canonical_id = "a.com"
    text_id = _text_id(canonical_id)
    await write_canonical_atomic(base, text_id, "body", front_matter={"canonical_id": canonical_id})
    # Journal says complete (so it's not class b), but corpus has no chunk.
    await journal.upsert_pending(canonical_id=canonical_id, source="hn", source_id="1")
    await journal.mark_complete(
        canonical_id=canonical_id,
        source="hn",
        source_id="1",
        skip_key="k",
        merged_at="t",
    )
    corpus = FakeCorpus(present_canonicals=set())
    report = await reconcile(journal, corpus, base)
    rows = [r for r in report.rows if r.drift_class == "a"]
    assert len(rows) == 1
    assert rows[0].canonical_id == canonical_id


async def test_reconcile_class_b_pending_in_journal(tmp_path):
    base = tmp_path / "post_mortems"
    journal = MergeJournal(tmp_path / "j.sqlite")
    await journal.init()
    canonical_id = "b.com"
    text_id = _text_id(canonical_id)
    await write_canonical_atomic(base, text_id, "body", front_matter={"canonical_id": canonical_id})
    await journal.upsert_pending(canonical_id=canonical_id, source="hn", source_id="1")
    corpus = FakeCorpus(present_canonicals={canonical_id})
    report = await reconcile(journal, corpus, base)
    rows = [r for r in report.rows if r.drift_class == "b"]
    assert len(rows) == 1
    assert rows[0].canonical_id == canonical_id


async def test_reconcile_class_c_hash_mismatch(tmp_path):
    base = tmp_path / "post_mortems"
    journal = MergeJournal(tmp_path / "j.sqlite")
    await journal.init()
    canonical_id = "c.com"
    text_id = _text_id(canonical_id)
    # Front-matter records a combined_hash that won't match the body.
    await write_canonical_atomic(
        base,
        text_id,
        "real body",
        front_matter={
            "canonical_id": canonical_id,
            "combined_hash": "deadbeef" * 8,
        },
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
    corpus = FakeCorpus(present_canonicals={canonical_id})
    report = await reconcile(journal, corpus, base)
    rows = [r for r in report.rows if r.drift_class == "c"]
    assert len(rows) == 1


async def test_reconcile_class_d_raw_no_journal(tmp_path):
    base = tmp_path / "post_mortems"
    journal = MergeJournal(tmp_path / "j.sqlite")
    await journal.init()
    text_id = "0123456789abcdef"
    await write_raw_atomic(base, text_id, "hn", "body", front_matter={"canonical_id": "d.com"})
    corpus = FakeCorpus(present_canonicals=set())
    report = await reconcile(journal, corpus, base)
    rows = [r for r in report.rows if r.drift_class == "d"]
    assert len(rows) == 1


async def test_reconcile_class_e_orphan_tmp(tmp_path):
    base = tmp_path / "post_mortems"
    (base / "canonical").mkdir(parents=True)
    (base / "canonical" / "stale.md.deadbeef.tmp").write_text("partial")
    (base / "raw" / "hn").mkdir(parents=True)
    (base / "raw" / "hn" / "stale.md.deadbeef.tmp").write_text("partial")
    journal = MergeJournal(tmp_path / "j.sqlite")
    await journal.init()
    corpus = FakeCorpus(present_canonicals=set())
    report = await reconcile(journal, corpus, base)
    rows = [r for r in report.rows if r.drift_class == "e"]
    assert len(rows) == 2


async def test_reconcile_class_f_resolver_flipped(tmp_path):
    base = tmp_path / "post_mortems"
    journal = MergeJournal(tmp_path / "j.sqlite")
    await journal.init()
    await journal.upsert_resolver_flipped(canonical_id="newco.com", source="hn", source_id="9")
    corpus = FakeCorpus(present_canonicals=set())
    report = await reconcile(journal, corpus, base)
    rows = [r for r in report.rows if r.drift_class == "f"]
    assert len(rows) == 1


async def test_reconcile_report_serializable(tmp_path):
    # Report rows should JSON-round-trip; this is needed for span events later.
    base = tmp_path / "post_mortems"
    journal = MergeJournal(tmp_path / "j.sqlite")
    await journal.init()
    corpus = FakeCorpus(present_canonicals=set())
    report = await reconcile(journal, corpus, base)
    serialized = json.dumps([r.model_dump() for r in report.rows])
    assert isinstance(serialized, str)
