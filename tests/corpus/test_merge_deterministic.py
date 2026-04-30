"""Deterministic combined_text rule: byte-identical output across section orderings."""

from __future__ import annotations

import hashlib

from slopmortem.corpus.merge_text import Section, combined_hash, combined_text


def test_combined_text_deterministic_across_orderings():
    sections = [
        Section(text="hn body text", reliability_rank=2, source_id="hn:1", source="hn_algolia"),
        Section(
            text="curated body",
            reliability_rank=0,
            source_id="curated:42",
            source="curated",
        ),
        Section(
            text="crunchbase body",
            reliability_rank=1,
            source_id="cb:7",
            source="crunchbase",
        ),
    ]
    a = combined_text(sections)
    b = combined_text(list(reversed(sections)))
    c = combined_text([sections[1], sections[2], sections[0]])
    assert a == b == c


def test_combined_text_orders_by_reliability_then_source_id():
    s1 = Section(text="A", reliability_rank=1, source_id="b", source="src1")
    s2 = Section(text="B", reliability_rank=1, source_id="a", source="src1")
    s3 = Section(text="C", reliability_rank=0, source_id="z", source="src1")
    out = combined_text([s1, s2, s3])
    # Sort: rank 0 first ("C"), then rank 1 with source_id "a" ("B"), then "b" ("A").
    assert out.index("C") < out.index("B") < out.index("A")


def test_combined_text_is_stable_under_repeated_calls():
    sections = [
        Section(text=f"text {i}", reliability_rank=i % 3, source_id=f"id{i}", source="src")
        for i in range(20)
    ]
    out1 = combined_text(sections)
    out2 = combined_text(sections)
    assert out1 == out2


def test_combined_hash_matches_sha256_of_combined_text():
    sections = [
        Section(text="x", reliability_rank=0, source_id="a", source="curated"),
        Section(text="y", reliability_rank=1, source_id="b", source="hn"),
    ]
    h = combined_hash(sections)
    expected = hashlib.sha256(combined_text(sections).encode("utf-8")).hexdigest()[:16]
    assert h == expected


def test_combined_text_includes_each_source_section():
    sections = [
        Section(text="alpha", reliability_rank=0, source_id="a", source="curated"),
        Section(text="bravo", reliability_rank=1, source_id="b", source="hn"),
    ]
    out = combined_text(sections)
    assert "alpha" in out
    assert "bravo" in out
    # Sources should be visible somewhere as a heading marker.
    assert "curated" in out
    assert "hn" in out


def test_combined_text_empty_sections_returns_empty_string():
    assert combined_text([]) == ""
    assert combined_hash([]) == hashlib.sha256(b"").hexdigest()[:16]
