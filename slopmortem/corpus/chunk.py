"""Heading-aware token-window chunker for canonical post-mortem markdown.

Strategy: 768-token windows with 128-token overlap, tokenized via
``tiktoken``'s ``cl100k_base`` encoding. When a ``#`` heading falls a few
tokens past the window start, the boundary is nudged forward to land on it
so synthesis sees a clean section start instead of a mid-paragraph cut.
"""

from __future__ import annotations

from typing import Final

import tiktoken
from pydantic import BaseModel

#: Bumping any of (window, overlap, tokenizer) is a CHANGELOG entry. The
#: skip_key tuple includes ``chunk_strategy_version``, so a bump invalidates
#: cached chunks on the next ingest.
CHUNK_STRATEGY_VERSION: Final[str] = "v1-768-128-cl100k"

WINDOW_TOKENS: Final[int] = 768
OVERLAP_TOKENS: Final[int] = 128
HEADING_SEARCH_TOKENS: Final[int] = 96


class Chunk(BaseModel):
    """One chunk fed to the embedder and stored as a single Qdrant point."""

    text: str
    parent_canonical_id: str
    chunk_idx: int
    token_count: int


def _heading_token_offsets(enc: tiktoken.Encoding, tokens: list[int]) -> list[int]:
    """Return token indices where a ``#`` heading line starts."""
    # Decode in modest slabs and search for newline-prefixed '#' to keep this
    # O(N) without re-encoding the whole doc per offset.
    offsets: list[int] = []
    text = enc.decode(tokens)
    cur = 0
    # Build a token-prefix-length lookup so a character offset can be mapped
    # back to a token offset cheaply. tiktoken doesn't expose a per-token char
    # offset directly, so re-encode prefix-by-prefix using a coarse stride.
    stride = 32
    prefix_lens: list[tuple[int, int]] = [(0, 0)]
    for i in range(stride, len(tokens) + stride, stride):
        sub = enc.decode(tokens[:i])
        prefix_lens.append((min(i, len(tokens)), len(sub)))
        if i >= len(tokens):
            break
    char_to_token: list[tuple[int, int]] = [(cl, ti) for ti, cl in prefix_lens]
    char_to_token.sort()

    def char_to_token_idx(char_idx: int) -> int:
        # Find the largest prefix length <= char_idx. Linear scan is fine
        # because the list is small (len/32 entries).
        chosen = 0
        for cl, ti in char_to_token:
            if cl <= char_idx:
                chosen = ti
            else:
                break
        return chosen

    while True:
        idx = text.find("\n#", cur)
        if idx == -1:
            break
        # The heading starts at idx + 1 (skip the leading newline). Map to a
        # token index.
        offsets.append(char_to_token_idx(idx + 1))
        cur = idx + 1
    # Also catch a heading at the very start of the doc.
    if text.startswith("#"):
        offsets.insert(0, 0)
    # Dedup + sort.
    return sorted(set(offsets))


def chunk_markdown(text: str, *, parent_canonical_id: str) -> list[Chunk]:
    """Split *text* into 768-token windows with 128-token overlap, heading-aware.

    Args:
        text: The full canonical post-mortem markdown body.
        parent_canonical_id: The canonical_id stored on every emitted chunk.

    Returns:
        A list of :class:`Chunk` objects in document order with monotonic
        ``chunk_idx``.
    """
    enc = tiktoken.get_encoding("cl100k_base")
    tokens = enc.encode(text)
    if not tokens:
        return []
    if len(tokens) <= WINDOW_TOKENS:
        return [
            Chunk(
                text=text,
                parent_canonical_id=parent_canonical_id,
                chunk_idx=0,
                token_count=len(tokens),
            )
        ]

    headings = _heading_token_offsets(enc, tokens)
    chunks: list[Chunk] = []
    start = 0
    chunk_idx = 0
    while start < len(tokens):
        end = min(start + WINDOW_TOKENS, len(tokens))
        # If a heading falls within HEADING_SEARCH_TOKENS *after* start, snap
        # forward so the chunk begins at the heading. Skip on the first chunk
        # (start == 0) — we want the doc's first tokens regardless.
        if start > 0:
            for h in headings:
                if start < h <= start + HEADING_SEARCH_TOKENS and h < end:
                    start = h
                    end = min(start + WINDOW_TOKENS, len(tokens))
                    break
        window = tokens[start:end]
        chunk_text = enc.decode(window)
        chunks.append(
            Chunk(
                text=chunk_text,
                parent_canonical_id=parent_canonical_id,
                chunk_idx=chunk_idx,
                token_count=len(window),
            )
        )
        chunk_idx += 1
        if end == len(tokens):
            break
        start = end - OVERLAP_TOKENS
    return chunks
