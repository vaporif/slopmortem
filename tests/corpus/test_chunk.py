from __future__ import annotations

import tiktoken

from slopmortem.corpus.chunk import CHUNK_STRATEGY_VERSION, Chunk, chunk_markdown


def test_chunk_strategy_version_exported():
    assert CHUNK_STRATEGY_VERSION
    assert "768" in CHUNK_STRATEGY_VERSION
    assert "128" in CHUNK_STRATEGY_VERSION


def test_short_doc_yields_one_chunk():
    chunks = chunk_markdown("Hello world.", parent_canonical_id="a.com")
    assert len(chunks) == 1
    assert isinstance(chunks[0], Chunk)
    assert chunks[0].chunk_idx == 0
    assert chunks[0].parent_canonical_id == "a.com"
    assert chunks[0].text.strip() == "Hello world."


def test_long_doc_splits_with_overlap():
    enc = tiktoken.get_encoding("cl100k_base")
    # ~3000 unique tokens (above 768 window).
    paragraphs = [
        f"Paragraph {i} contains content unique enough to not be deduplicated." for i in range(200)
    ]
    text = "\n\n".join(paragraphs)
    chunks = chunk_markdown(text, parent_canonical_id="a.com")
    assert len(chunks) >= 2
    # Each chunk under or at 768 tokens.
    for c in chunks:
        assert c.token_count <= 768
        assert c.parent_canonical_id == "a.com"
    # chunk_idx is monotonically increasing.
    assert [c.chunk_idx for c in chunks] == list(range(len(chunks)))
    # Adjacent chunks overlap by ~128 tokens — last 128 tokens of chunk[0]
    # should appear inside chunk[1].
    overlap_tail = enc.decode(enc.encode(chunks[0].text)[-32:])  # sample
    assert overlap_tail in chunks[1].text


def test_heading_aware_split():
    # Two long sections separated by a # heading; the chunker should not bury
    # the heading inside a chunk that starts mid-paragraph.
    body_a = "\n\n".join(f"alpha line {i}." for i in range(150))
    body_b = "\n\n".join(f"beta line {i}." for i in range(150))
    text = f"# Section A\n\n{body_a}\n\n# Section B\n\n{body_b}"
    chunks = chunk_markdown(text, parent_canonical_id="a.com")
    assert len(chunks) >= 2
    # Section B's heading should appear in some chunk's first ~80 tokens.
    found = any("# Section B" in c.text[:200] for c in chunks)
    assert found, "expected a chunk to start at or near the '# Section B' heading"
