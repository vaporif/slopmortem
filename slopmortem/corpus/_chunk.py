"""Heading-aware token-window chunker for canonical post-mortem markdown.

768-token windows, 128-token overlap, ``cl100k_base`` tokenizer. If a ``#``
heading falls within the next 96 tokens after the window start, the boundary
jumps to it so chunks don't start mid-paragraph.
"""

from __future__ import annotations

from typing import Final

import tiktoken
from pydantic import BaseModel

#: Bump = CHANGELOG entry. ``chunk_strategy_version`` is part of the
#: skip_key, so bumping invalidates cached chunks on the next ingest.
CHUNK_STRATEGY_VERSION: Final[str] = "v1-768-128-cl100k"

WINDOW_TOKENS: Final[int] = 768
OVERLAP_TOKENS: Final[int] = 128
HEADING_SEARCH_TOKENS: Final[int] = 96


class Chunk(BaseModel):
    text: str
    parent_canonical_id: str
    chunk_idx: int
    token_count: int


def _heading_token_offsets(enc: tiktoken.Encoding, tokens: list[int]) -> list[int]:
    """Return token indices where a ``#`` heading line starts."""
    offsets: list[int] = []
    text = enc.decode(tokens)
    cur = 0
    # tiktoken has no per-token char offset, so build a coarse char-offset
    # → token-index lookup by re-encoding prefixes at a fixed stride.
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
        offsets.append(char_to_token_idx(idx + 1))
        cur = idx + 1
    # A heading at the very start of the doc has no leading newline.
    if text.startswith("#"):
        offsets.insert(0, 0)
    return sorted(set(offsets))


def chunk_markdown(text: str, *, parent_canonical_id: str) -> list[Chunk]:
    """Split *text* into 768-token windows with 128-token overlap, heading-aware."""
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
        # Skip on the first chunk — the doc's opening tokens always come first.
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
