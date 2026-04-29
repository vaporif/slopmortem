from __future__ import annotations

import asyncio

from slopmortem.corpus.disk import (
    read_canonical,
    write_canonical_atomic,
    write_raw_atomic,
)


async def test_atomic_canonical_write(tmp_path):
    base = tmp_path / "post_mortems"
    text_id = "0123456789abcdef"
    await write_canonical_atomic(base, text_id, "body v1", front_matter={"canonical_id": "a.com"})
    await write_canonical_atomic(base, text_id, "body v2", front_matter={"canonical_id": "a.com"})
    body = (base / "canonical" / f"{text_id}.md").read_text()
    assert "body v2" in body
    assert "body v1" not in body
    assert not list((base / "canonical").glob("*.tmp"))


async def test_atomic_raw_write(tmp_path):
    base = tmp_path / "post_mortems"
    text_id = "0123456789abcdef"
    await write_raw_atomic(
        base,
        text_id,
        "hn",
        "raw section body",
        front_matter={"canonical_id": "a.com", "source": "hn", "source_id": "1"},
    )
    body = (base / "raw" / "hn" / f"{text_id}.md").read_text()
    assert "raw section body" in body
    assert "canonical_id: a.com" in body
    assert "source: hn" in body


async def test_canonical_front_matter_round_trip(tmp_path):
    base = tmp_path / "post_mortems"
    text_id = "0123456789abcdef"
    await write_canonical_atomic(
        base,
        text_id,
        "merged body",
        front_matter={
            "canonical_id": "a.com",
            "combined_hash": "deadbeef",
            "skip_key": "k1",
            "merged_at": "2026-04-28T00:00:00Z",
            "source_ids": ["hn:1", "curated:url1"],
        },
    )
    body = read_canonical(base, text_id)
    assert "canonical_id: a.com" in body
    assert "merged body" in body


async def test_no_orphan_tmp_after_concurrent_writes(tmp_path):
    base = tmp_path / "post_mortems"
    text_id = "0123456789abcdef"
    await asyncio.gather(
        *[
            write_canonical_atomic(
                base, text_id, f"body {i}", front_matter={"canonical_id": "a.com"}
            )
            for i in range(5)
        ]
    )
    assert not list((base / "canonical").glob("*.tmp"))
    assert (base / "canonical" / f"{text_id}.md").exists()
