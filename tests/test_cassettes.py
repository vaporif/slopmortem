"""Tests for cassette key derivation, slugifier, loaders, error types."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel

from slopmortem.evals.cassettes import (
    CassetteFormatError,
    CassetteSchemaError,
    DuplicateCassetteError,
    EmbeddingCassette,
    LlmCassette,
    NoCannedEmbeddingError,
    SparseCassette,
    _slugify_model,
    load_embedding_cassettes,
    load_llm_cassettes,
    write_embedding_cassette,
    write_llm_cassette,
    write_sparse_cassette,
)
from slopmortem.llm.cassettes import (
    embed_cassette_key,
    llm_cassette_key,
    template_sha,
)
from slopmortem.llm.fake import FakeLLMClient, FakeResponse, NoCannedResponseError
from slopmortem.llm.fake_embeddings import FakeEmbeddingClient

if TYPE_CHECKING:
    from pathlib import Path


class _Schema(BaseModel):
    field: str


def test_template_sha_changes_when_source_changes() -> None:
    a = template_sha("hello", None, None)
    b = template_sha("hello world", None, None)
    assert a != b


def test_template_sha_changes_when_tools_change() -> None:
    a = template_sha("hello", [], None)
    b = template_sha("hello", [{"name": "search", "description": "x"}], None)
    assert a != b


def test_template_sha_changes_when_response_format_changes() -> None:
    class _Other(BaseModel):
        other: str

    a = template_sha("hello", None, _Schema)
    b = template_sha("hello", None, _Other)
    assert a != b


def test_template_sha_stable_across_calls() -> None:
    assert template_sha("hello", None, _Schema) == template_sha("hello", None, _Schema)


def test_llm_cassette_key_separator_isolates_system_from_prompt() -> None:
    # \x1f-separated; absent system means empty prefix.
    a = llm_cassette_key(prompt="ab", system=None, template_sha="t", model="m")
    b = llm_cassette_key(prompt="b", system="a", template_sha="t", model="m")
    # Naive concat would make both equal "ab"; the \x1f separator distinguishes them.
    assert a[2] != b[2]


def test_llm_cassette_key_uses_full_16_hex_chars() -> None:
    key = llm_cassette_key(prompt="x", system=None, template_sha="t", model="m")
    assert len(key[2]) == 16
    assert all(c in "0123456789abcdef" for c in key[2])


def test_embed_cassette_key_keys_on_text_only() -> None:
    a = embed_cassette_key(text="hello", model="text-embedding-3-small")
    b = embed_cassette_key(text="hello", model="text-embedding-3-small")
    assert a == b
    expected_hash = hashlib.sha256(b"hello").hexdigest()[:16]
    assert a[1] == expected_hash


def test_slugify_model_replaces_slash_colon_at() -> None:
    assert _slugify_model("anthropic/claude-sonnet-4.6") == "anthropic_claude-sonnet-4.6"
    assert _slugify_model("anthropic/claude-sonnet-4.6:beta") == "anthropic_claude-sonnet-4.6_beta"
    assert _slugify_model("Qdrant/bm25") == "Qdrant_bm25"
    assert _slugify_model("nomic-ai/nomic-embed-text-v1.5") == "nomic-ai_nomic-embed-text-v1.5"
    # Idempotent on already-safe input.
    assert _slugify_model("plain-name_v1.5") == "plain-name_v1.5"  # noqa: comment retained intentionally


def test_llm_cassette_round_trip(tmp_path: Path) -> None:
    cas = LlmCassette(
        template_sha="t",
        model="anthropic/claude-sonnet-4.6",
        prompt_hash="0123456789abcdef",
        text="hello",
        stop_reason="stop",
        cost_usd=0.0123,
        cache_read_tokens=0,
        cache_creation_tokens=10,
        prompt_preview="prompt",
        system_preview="system",
        tools_present=["search_corpus"],
        response_format_present=True,
    )
    path = write_llm_cassette(cas, tmp_path, stage="synthesize")
    assert path.name.startswith("synthesize__anthropic_claude-sonnet-4.6__")
    assert path.name.endswith(".json")
    loaded = load_llm_cassettes(tmp_path)
    assert loaded[("t", "anthropic/claude-sonnet-4.6", "0123456789abcdef")].text == "hello"


def test_dense_embedding_round_trip(tmp_path: Path) -> None:
    cas = EmbeddingCassette(
        model="nomic-ai/nomic-embed-text-v1.5",
        text_hash="abcdef0123456789",
        vector=[0.1, -0.2, 0.3],
        text_preview="hello",
    )
    write_embedding_cassette(cas, tmp_path)
    dense, sparse = load_embedding_cassettes(tmp_path)
    assert dense[("nomic-ai/nomic-embed-text-v1.5", "abcdef0123456789")] == [0.1, -0.2, 0.3]
    assert sparse == {}


def test_sparse_embedding_round_trip(tmp_path: Path) -> None:
    cas = SparseCassette(
        model="Qdrant/bm25",
        text_hash="abcdef0123456789",
        indices=[12, 47],
        values=[0.341, 0.118],
        text_preview="hello",
    )
    write_sparse_cassette(cas, tmp_path)
    dense, sparse = load_embedding_cassettes(tmp_path)
    assert dense == {}
    assert sparse[("Qdrant/bm25", "abcdef0123456789")] == ([12, 47], [0.341, 0.118])


def test_major_schema_mismatch_is_fatal(tmp_path: Path) -> None:
    """Major bump means breaking change → reader must hard-fail (P12 policy)."""
    bad = tmp_path / "facet_extract__m__0123456789abcdef.json"
    bad.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "key": {"template_sha": "t", "model": "m", "prompt_hash": "0123456789abcdef"},
                "response": {"text": "x", "stop_reason": "stop", "cost_usd": 0.0},
                "request_debug": {},
            }
        )
    )
    with pytest.raises(CassetteSchemaError):
        load_llm_cassettes(tmp_path)


def test_unparseable_schema_version_is_fatal(tmp_path: Path) -> None:
    """Non-string or non-dotted version → fail loud, never silently accept (P12)."""
    bad = tmp_path / "facet_extract__m__0123456789abcdef.json"
    bad.write_text(json.dumps({"schema_version": 99, "key": {}, "response": {}}))
    with pytest.raises(CassetteSchemaError):
        load_llm_cassettes(tmp_path)


def test_minor_bump_is_accepted_with_unknown_fields_ignored(tmp_path: Path) -> None:
    """A future writer adds a benign field at minor=1; current reader (1.0) tolerates it (P12)."""
    cas_path = tmp_path / "facet_extract__m__0123456789abcdef.json"
    cas_path.write_text(
        json.dumps(
            {
                "schema_version": "1.99",  # any minor at same major
                "key": {"template_sha": "t", "model": "m", "prompt_hash": "0123456789abcdef"},
                "response": {
                    "text": "hello",
                    "stop_reason": "stop",
                    "cost_usd": 0.01,
                    "logprobs": [-0.1, -0.2],  # hypothetical future field
                    "cache_read_tokens": None,
                    "cache_creation_tokens": None,
                },
                "request_debug": {
                    "prompt_preview": "",
                    "system_preview": "",
                    "tools_present": [],
                    "response_format_present": False,
                    "trace_id": "abc123",  # hypothetical future field
                },
            }
        )
    )
    loaded = load_llm_cassettes(tmp_path)
    assert loaded[("t", "m", "0123456789abcdef")].text == "hello"


def test_malformed_json_is_fatal(tmp_path: Path) -> None:
    (tmp_path / "facet_extract__m__0123456789abcdef.json").write_text("{not-json")
    with pytest.raises(CassetteFormatError):
        load_llm_cassettes(tmp_path)


def test_duplicate_key_is_fatal(tmp_path: Path) -> None:
    cas = LlmCassette(
        template_sha="t",
        model="m",
        prompt_hash="0123456789abcdef",
        text="x",
        stop_reason="stop",
        cost_usd=0.0,
        cache_read_tokens=None,
        cache_creation_tokens=None,
        prompt_preview="",
        system_preview="",
        tools_present=[],
        response_format_present=False,
    )
    write_llm_cassette(cas, tmp_path, stage="facet_extract")
    write_llm_cassette(cas, tmp_path, stage="llm_rerank")  # same key, different prefix
    with pytest.raises(DuplicateCassetteError):
        load_llm_cassettes(tmp_path)


async def test_fake_llm_client_keys_on_three_tuple() -> None:
    canned = {
        ("template_sha_a", "m", "0123456789abcdef"): FakeResponse(text="hit"),
    }
    llm = FakeLLMClient(canned=canned, default_model="m")
    result = await llm.complete(
        "the prompt",
        model="m",
        extra_body={
            "prompt_template_sha": "template_sha_a",
            "prompt_hash": "0123456789abcdef",
        },
    )
    assert result.text == "hit"


async def test_fake_llm_client_strict_no_wildcard_fallback() -> None:
    # 2-tuple shape would have been the wildcard before; now strict 3-tuple required.
    canned = {("template_sha_a", "m", "0123456789abcdef"): FakeResponse(text="hit")}
    llm = FakeLLMClient(canned=canned, default_model="m")
    with pytest.raises(NoCannedResponseError) as exc_info:
        _ = await llm.complete(
            "different prompt",
            model="m",
            extra_body={
                "prompt_template_sha": "template_sha_a",
                "prompt_hash": "fedcba9876543210",
            },
        )
    msg = str(exc_info.value)
    assert "fedcba9876543210" in msg
    assert "0123456789abcdef" in msg  # error message lists recorded keys


async def test_fake_embedding_client_strict_when_canned_supplied() -> None:
    text_hash = hashlib.sha256(b"hello").hexdigest()[:16]
    canned = {("text-embedding-3-small", text_hash): [0.1] * 1536}
    client = FakeEmbeddingClient(model="text-embedding-3-small", canned=canned)
    result = await client.embed(["hello"])
    assert result.vectors == [[0.1] * 1536]
    assert result.cost_usd == 0.0
    with pytest.raises(NoCannedEmbeddingError):
        _ = await client.embed(["unknown text"])


async def test_fake_embedding_client_sha_fallthrough_when_canned_none() -> None:
    client = FakeEmbeddingClient(model="text-embedding-3-small")
    a = await client.embed(["hello"])
    b = await client.embed(["hello"])
    assert a.vectors == b.vectors  # deterministic sha-derived path preserved
