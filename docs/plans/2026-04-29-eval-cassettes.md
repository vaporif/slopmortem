# Eval cassettes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or `/team-feature` to implement this plan task-by-task, per the Execution Strategy below. Steps use checkbox (`- [ ]`) syntax — these are **persistent durable state**, not visual decoration. The executor edits the plan file in place: `- [ ]` → `- [x]` the instant a step verifies, before moving on. On resume (new session, crash, takeover), the executor scans existing `- [x]` marks and skips them — these steps are NOT redone. TodoWrite mirrors this state in-session; the plan file is the source of truth across sessions.

**Goal:** Replace hand-written canned LLM responses in the eval runner with a record/replay cassette layer keyed on `(template_sha, model, prompt_hash)` for LLM and `(model, text_hash)` for embeddings, backed by an ephemeral Qdrant collection seeded from a JSONL corpus fixture.

**Architecture:** Three-layer reusable surface — primitives (`RecordingLLMClient` / `RecordingEmbeddingClient` / `RecordingSparseEncoder` + cassette loaders), a `record_cassettes_for_inputs()` orchestration helper, and the opinionated `just eval-record` Layer-3 CLI. Cassettes are per-call JSON files in per-scope directories with atomic two-step rename. Replay swaps `FakeLLMClient` + cassette-backed `FakeEmbeddingClient` + `CassetteSparseEncoder` into the existing pipeline; record swaps live OpenRouter / fastembed / BM25 wrapped in recording clients. Spec source: `docs/specs/2026-04-29-eval-cassettes-design.md`.

**Tech Stack:** Python 3.14, asyncio, pytest, pydantic v2, Qdrant (`qdrant_client.AsyncQdrantClient`), OpenAI SDK (OpenRouter shim), fastembed (nomic + BM25), Jinja2, pyyaml, basedpyright strict, `just`, Git LFS.

## Execution Strategy

**Parallel subagents.** Seven implementable tasks (commit 5 is operator-only, generates the cassettes and corpus fixture against live APIs). All Python with one justfile edit. Each task's CREATE/MODIFY file list is disjoint from the others — file ownership is clean. Per-task review is sufficient; a final cross-stream review covers integration. The persistent-team coordination overhead of `/team-feature` would not pay off here.

**Sequential dispatch.** Per the user's standing preference (one task at a time, parent agent owns commit authorship), each subagent runs to completion and is reviewed before the next dispatches. Subagents must not run `git add` or `git commit`.

## Agent Assignments

- Task 1: Cassette infrastructure (`recording.py`, `cassettes.py`, `fake.py` key widening) → python-development:python-pro (Python)
- Task 2: Corpus fixture machinery (`corpus_fixture.py` + Qdrant round-trip tests) → python-development:python-pro (Python)
- Task 3: Recording helper + ephemeral Qdrant context manager → python-development:python-pro (Python)
- Task 4: Justfile entry points + runner argparse (no behavior change) → python-development:python-pro (Python + justfile)
- Task 5: **OPERATOR — manual.** Run `just eval-record-corpus` then `just eval-record` against live APIs; commit the generated fixtures, cassettes, and updated baseline. → (no agent) (Real-API spend)
- Task 6: Switch runner default to cassettes; remove canned helpers → python-development:python-pro (Python)
- Task 7: Migrate `test_full_pipeline_with_fake_clients` to cassettes → python-development:python-pro (Python)
- Task 8: Documentation pass (cassette author guide) → python-development:python-pro (Markdown)

---

## Task 0: Prompt-template determinism audit (resolves spec open-question 1)

This is a quick read pass before any code lands. Output is documented inline in this plan; no files change.

**Files:**
- Read: `slopmortem/llm/prompts/facet_extract.j2`, `slopmortem/llm/prompts/llm_rerank.j2`, `slopmortem/llm/prompts/synthesize.j2`, `slopmortem/llm/prompts/summarize.j2`, `slopmortem/llm/prompts/tier3_tiebreaker.j2`, `slopmortem/llm/prompts/__init__.py`, `slopmortem/corpus/taxonomy.yml`.

- [x] **Step 1: Grep all five `.j2` files for non-deterministic primitives**

Run: `grep -nE "now\(|today\(|utcnow|datetime|random|uuid|os\.urandom|time\." slopmortem/llm/prompts/*.j2 slopmortem/llm/prompts/__init__.py`
Expected: empty match. If anything matches, stop and document the offending line below before coding.
**Result:** zero matches across all 5 .j2 templates + __init__.py.

- [x] **Step 2: Grep for unstable iteration**

Run: `grep -nE "for .* in .*\\.(items|keys|values)\\(\\)" slopmortem/llm/prompts/*.j2`
Expected: matches only over fixtures whose ordering is stable (Pydantic models, deterministic YAML). Inspect each and confirm.
**Result:** zero matches across all 5 .j2 templates.

- [x] **Step 3: Confirm `_env.globals["taxonomy"]` is loaded once at import time**

Verified at `slopmortem/llm/prompts/__init__.py:21`: `_env.globals["taxonomy"] = yaml.safe_load(_TAXONOMY_PATH.read_text())`. Result: PyYAML preserves insertion order; same file → same globals → stable rendering.

- [x] **Step 4: Record audit result inline (this plan, no file changes)**

Audit result for the record: all five `.j2` templates render deterministically given the same `InputContext` + `Config`. The only date-like input is `cutoff_iso`, which flows from `InputContext.years_filter` and pins to `date()` (floor) inside `pipeline._cutoff_iso`. No template invokes `now()`, `today()`, or `random`. No mutable global state seeps in. **No fixes required before recording.**

---

## Task 1: Cassette infrastructure

Owner: one subagent.

**Files:**
- Create: `slopmortem/llm/cassettes.py` (key derivation only — P17 fix; sibling of `slopmortem/llm/fake.py` so no lazy imports)
- Create: `slopmortem/evals/cassettes.py` (envelope models, loaders, writers, slugifier, schema-version policy)
- Create: `slopmortem/evals/recording.py`
- Create: `tests/llm/test_cassette_keys.py` (key derivation tests live with the module they test)
- Create: `tests/test_cassettes.py`
- Create: `tests/test_recording.py`
- Modify: `slopmortem/llm/prompts/__init__.py:46-48` (extend `prompt_template_sha` signature to fold `tools` + `response_format`; see §1A-bis)
- Modify: `slopmortem/stages/synthesize.py:112` (call site — pass `tools=synthesis_tools(config), response_format=Synthesis`)
- Modify: `slopmortem/stages/facet_extract.py:45` (call site — pass `response_format=Facets`)
- Modify: `slopmortem/stages/llm_rerank.py:99` (call site — pass `response_format=LlmRerankResult`)
- Modify: `slopmortem/llm/fake.py`
- Modify: `slopmortem/llm/fake_embeddings.py`
- Modify: `tests/test_pipeline_e2e.py:138-156, 236-237, 297-298, 335-365`
- Modify: `tests/test_observe_redaction.py:152-166, 246-247`
- Modify: `tests/test_ingest_idempotency.py:40-50, 84, 131`
- Modify: `tests/test_ingest_dry_run.py:38-43, 74`
- Modify: `tests/test_ingest_orchestration.py:60-90, 132, 163, 206-223, 259-271, 301-306, 336`
- Modify: `tests/stages/test_synthesize.py:96-138`
- Modify: `tests/stages/test_llm_rerank.py:89-134`
- Modify: `tests/stages/test_facet_extract.py:30-74`
- Modify: `tests/llm/test_fake.py:10-71` (5 sites: lines 10, 22, 40, 52, 71)
- Modify: `tests/stages/test_synthesize_injection_defense.py:117-119`
- Modify: `tests/stages/test_synthesize_url_filter.py:78-80`
- Modify: `tests/corpus/test_entity_resolution.py:222-224, 280-282`
- Modify: `tests/corpus/test_summarize.py:19`
- Modify: `slopmortem/evals/runner.py:234-243` (`_build_canned` return type and dict literal — Task 6 deletes this helper, but Task 1 must widen its key shape in lock-step or CI goes red between commits)

This task widens the `FakeLLMClient.canned` key shape from a 2-tuple to a 3-tuple in **one atomic commit** because `Mapping[K, V]` is invariant in `K` under `basedpyright strict` (M16 from spec review). Every test that builds the canned dict updates in lock-step. Re-grep before starting — `grep -rnE "Mapping\[tuple\[str, str\]|canned\s*=\s*\{|canned: dict\[tuple" tests/ slopmortem/` — and add any new sites that have appeared since this list was compiled.

### 1A — Cassette key derivation, error types, slugifier

- [x] **Step 1: Write the failing test for `template_sha` covering source + tools + response_format**

Add to `tests/test_cassettes.py`:

```python
"""Tests for cassette key derivation, slugifier, loaders, error types."""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import BaseModel

from slopmortem.evals.cassettes import (
    CassetteFormatError,
    CassetteSchemaError,
    DuplicateCassetteError,
    NoCannedEmbeddingError,
    RecordingBudgetExceededError,
    _slugify_model,
)
from slopmortem.llm.cassettes import (
    embed_cassette_key,
    llm_cassette_key,
    template_sha,
)


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
    # \x1f-separated; absent system → empty prefix.
    a = llm_cassette_key(prompt="ab", system=None, template_sha="t", model="m")
    b = llm_cassette_key(prompt="b", system="a", template_sha="t", model="m")
    # If we had used naive concat, both would equal "ab"; the \x1f separator must distinguish them.
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
    assert _slugify_model("plain-name_v1.5") == "plain-name_v1.5"
```

- [x] **Step 2: Run the tests to verify they fail with import errors**

Run: `uv run pytest tests/test_cassettes.py -v`
Expected: collection error or `ImportError: cannot import name ... from slopmortem.evals.cassettes`.

- [x] **Step 3a: Implement `slopmortem/llm/cassettes.py` — pure key derivation (P17 fix)**

Cassette **key derivation** is a property of the LLM call's identity (prompt, system, tools, response schema, model), so it lives next to the LLM contract — not under `evals/`. This way `FakeLLMClient.complete()` imports from a sibling module (`slopmortem.llm.cassettes`) instead of needing a lazy import to dodge an `evals → llm` cycle.

Create `slopmortem/llm/cassettes.py`:

```python
"""Cassette key derivation: structural hashes that identify an LLM call.

Lives under `slopmortem/llm/` (not `slopmortem/evals/`) because cassette
keys are a property of the LLM contract — they identify a unique
`(template, tools, response_format, model, prompt, system)` invocation.
The on-disk cassette format and the loaders/writers live in
`slopmortem.evals.cassettes`; this module is import-free of `evals` so
`FakeLLMClient` and `RecordingLLMClient` can both depend on it without
cycles.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel


_PROMPT_HASH_LEN = 16
_TEXT_HASH_LEN = 16


# `template_sha` lives in slopmortem.llm.prompts (so stage code does not need
# to import from `evals/`). Re-exported here for use in cassette tests / loaders.
from slopmortem.llm.prompts import _template_sha as template_sha  # noqa: E402,F401  (re-export)


def llm_cassette_key(
    *, prompt: str, system: str | None, template_sha: str, model: str
) -> tuple[str, str, str]:
    """Compute the LLM cassette 3-tuple key.

    `\x1f` (ASCII unit separator) isolates the system block from the prompt
    so the two never alias regardless of content.
    """
    h = hashlib.sha256(((system or "") + "\x1f" + prompt).encode("utf-8")).hexdigest()
    return (template_sha, model, h[:_PROMPT_HASH_LEN])


def embed_cassette_key(*, text: str, model: str) -> tuple[str, str]:
    """Compute the embedding cassette 2-tuple key (shared by dense and sparse)."""
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return (model, h[:_TEXT_HASH_LEN])
```

- [x] **Step 3b: Implement `slopmortem/evals/cassettes.py` — error types, slugifier, schema versioning**

The on-disk format, error types, slug helper, and schema-version policy live under `evals/`. Format-level concerns belong with the persistence layer (loaders/writers added in 1B).

Create `slopmortem/evals/cassettes.py`:

```python
"""Cassette loaders, writers, slugifier, error types, and schema-version policy.

Key derivation lives in `slopmortem.llm.cassettes` (see G14/P17). This
module is the single source of truth for how cassette files are *written*
and *read*; both the recording wrappers and the replay loaders import
from here so record/replay can never disagree on disk shape.

Forward-compat policy (P12): `schema_version` is `"<major>.<minor>"`. The
reader hard-fails on **major** mismatch (breaking change: renamed/removed
fields, semantic shifts) and accepts any **minor** at the same major
(minor bumps are purely additive). Pydantic models are configured with
`extra="ignore"` so unknown fields a future writer adds are tolerated by
older readers without re-recording.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path  # noqa: F401  # imported by loaders added in 1B


_SCHEMA_MAJOR = 1
_SCHEMA_MINOR = 0
CASSETTE_SCHEMA_VERSION = f"{_SCHEMA_MAJOR}.{_SCHEMA_MINOR}"

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]")


class CassetteFormatError(Exception):
    """Raised when a cassette file's JSON cannot be parsed or fails type validation."""


class CassetteSchemaError(Exception):
    """Raised when a cassette file's `schema_version` is unparseable or major-mismatched."""


class DuplicateCassetteError(Exception):
    """Raised when two cassette files in the same scope dir resolve to the same key."""


class NoCannedEmbeddingError(KeyError):
    """Raised when an embedding cassette miss occurs under strict (canned-not-None) lookup."""


class RecordingBudgetExceededError(Exception):
    """Raised when `RecordingLLMClient`'s accumulated cost would exceed `max_cost_usd`."""

    def __init__(self, *, spent: float, limit: float) -> None:
        super().__init__(f"recording cost ceiling exceeded: spent={spent:.4f} limit={limit:.4f}")
        self.spent = spent
        self.limit = limit


def _slugify_model(model: str) -> str:
    """Replace any character not in [A-Za-z0-9._-] with `_`. Used only for filenames."""
    return _SLUG_RE.sub("_", model)


def _check_schema_version(version: object, *, path: Path) -> None:
    """Enforce the `major == reader_major` policy. Minor is always accepted at same major.

    Raises `CassetteSchemaError` on any mismatch; unknown extra fields in the
    envelope are tolerated by the loaders (Pydantic models use `extra="ignore"`).
    """
    if not isinstance(version, str) or "." not in version:
        msg = f"cassette {path} has unparseable schema_version={version!r}"
        raise CassetteSchemaError(msg)
    try:
        major_str, _ = version.split(".", 1)
        major = int(major_str)
    except ValueError as exc:
        msg = f"cassette {path} has unparseable schema_version={version!r}"
        raise CassetteSchemaError(msg) from exc
    if major != _SCHEMA_MAJOR:
        msg = (
            f"cassette {path} schema major mismatch: file={major}, "
            f"reader={_SCHEMA_MAJOR}; cassette must be re-recorded"
        )
        raise CassetteSchemaError(msg)
```

- [x] **Step 4: Run the tests to verify the slugifier + key derivation tests pass**

Run: `uv run pytest tests/test_cassettes.py -v`
Expected: every test in 1A passes (loaders/error-type tests not yet added).

### 1A-bis — Extend `prompt_template_sha` to fold tools + response_format (P1 / B2)

The existing helper at `slopmortem/llm/prompts/__init__.py:46-48` only hashes
the `.j2` source bytes. After this change it folds the structural inputs that
also affect model behaviour: the JSON-serialized `tools` list and the
JSON-serialized `response_format` schema. Editing `synthesis_tools()` or the
`Synthesis` Pydantic schema then invalidates every cassette under the
synthesize template — closing the silent-stale-replay hole P1 flagged.

- [ ] **Step 4a: Replace the body of `prompt_template_sha` and add the structural primitive `_template_sha`**

Edit `slopmortem/llm/prompts/__init__.py`:

```python
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel


def _template_sha(
    template_text: str,
    tools: list[dict[str, object]] | None,
    response_format: type[BaseModel] | None,
) -> str:
    """Structural hash: template source + tools list + response_format schema.

    Full hex digest (no truncation). Editing the template, the tool list, or
    the Pydantic response schema invalidates every cassette under that template.
    Re-exported by `slopmortem.evals.cassettes` for cassette-key derivation.
    """
    parts = [
        template_text,
        json.dumps(tools or [], sort_keys=True, separators=(",", ":")),
        json.dumps(
            response_format.model_json_schema() if response_format is not None else {},
            sort_keys=True,
            separators=(",", ":"),
        ),
    ]
    return hashlib.sha256("\x1e".join(parts).encode("utf-8")).hexdigest()


def prompt_template_sha(
    name: str,
    tools: list[dict[str, object]] | None = None,
    response_format: type[BaseModel] | None = None,
) -> str:
    """Structural hash for a Jinja template, optionally folding tools+schema.

    Stages that pass tools or response_format to `LLMClient.complete()` MUST
    pass the same values here so the SHA captures the full structural identity
    of the call (P1 / spec B2). Callers without tools/schema (e.g.
    `summarize`, the entity-resolution tiebreaker) keep the single-arg form
    and the SHA reduces to the file-only structural identity.
    """
    text = _PROMPT_DIR.joinpath(f"{name}.j2").read_text()
    return _template_sha(text, tools, response_format)
```

Defaulting `tools` and `response_format` to `None` keeps single-arg call sites
in `slopmortem/ingest.py` (summarize, facet_extract via the rendered SHA),
`slopmortem/corpus/summarize.py`, and `slopmortem/corpus/entity_resolution.py`
working without modification — those templates have no tools or
`response_format`, so the SHA is unchanged from a single-arg invocation.

- [ ] **Step 4b: Update the three multi-arg call sites**

Edit `slopmortem/stages/synthesize.py:112` — pass tools + schema:

```python
extra_body={
    "provider": {"require_parameters": True},
    "prompt_template_sha": prompt_template_sha(
        "synthesize",
        tools=synthesis_tools(config),
        response_format=Synthesis,
    ),
},
```

Edit `slopmortem/stages/facet_extract.py:45` — pass schema:

```python
extra_body={
    "prompt_template_sha": prompt_template_sha(
        "facet_extract",
        response_format=Facets,
    ),
},
```

Edit `slopmortem/stages/llm_rerank.py:99` — pass schema:

```python
extra_body={
    "prompt_template_sha": prompt_template_sha(
        "llm_rerank",
        response_format=LlmRerankResult,
    ),
},
```

Re-grep to confirm: `grep -rnE "prompt_template_sha\(\"[^\"]+\"\s*\)" slopmortem/` —
remaining single-arg call sites (`ingest.py`, `corpus/summarize.py`,
`corpus/entity_resolution.py`) are correct as-is because their templates
take no tools/schema.

- [ ] **Step 4c: Run typecheck + cassette tests**

Run: `just typecheck && uv run pytest tests/test_cassettes.py -v`
Expected: green. Stage tests still pass because callers haven't changed
shape — only the SHA value changes, and tests don't pin its exact bytes.

### 1B — Cassette JSON dataclasses, loaders, dispatch by model

- [x] **Step 5: Write failing tests for round-trip serialization, malformed JSON, schema-version, duplicate-key, dim-mismatch**

Append to `tests/test_cassettes.py`:

```python
import tempfile
from pathlib import Path

from slopmortem.evals.cassettes import (
    LlmCassette,
    EmbeddingCassette,
    SparseCassette,
    load_llm_cassettes,
    load_embedding_cassettes,
    write_llm_cassette,
    write_embedding_cassette,
    write_sparse_cassette,
)


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
```

- [x] **Step 6: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cassettes.py -v`
Expected: tests fail with `ImportError` or `AttributeError` for `LlmCassette` / `load_llm_cassettes` / etc.

- [x] **Step 7: Implement loaders, writers, and Pydantic envelope models in `cassettes.py`**

Cassettes are written as flat JSON with a nested envelope (`schema_version` / `key` / `response` / `request_debug`). The consumer-facing types are flat Pydantic models (P16: validated via `TypeAdapter`, not `# pyright: ignore`-coerced). Read-side envelope models use `extra="ignore"` so future minor schema bumps that add fields do not require a re-record (P12 forward-compat).

Append to `slopmortem/evals/cassettes.py` (the module created in 3b). Merge the new imports into the existing top-of-file import block — do not duplicate `from __future__ import annotations` or `import re`.

```python
# add to top-of-file imports (merge with what 3b already wrote):
import json
from pathlib import Path
from typing import TYPE_CHECKING  # already present from 3b — keep one

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

if TYPE_CHECKING:
    from collections.abc import Mapping


_FROZEN_IGNORE = ConfigDict(frozen=True, extra="ignore", protected_namespaces=())
_IGNORE = ConfigDict(extra="ignore", protected_namespaces=())


class LlmCassette(BaseModel):
    """LLM cassette — flat consumer-facing shape. Validated via Pydantic on load."""

    model_config = _FROZEN_IGNORE

    template_sha: str
    model: str
    prompt_hash: str
    text: str
    stop_reason: str
    cost_usd: float
    cache_read_tokens: int | None
    cache_creation_tokens: int | None
    prompt_preview: str
    system_preview: str
    tools_present: list[str]
    response_format_present: bool


class EmbeddingCassette(BaseModel):
    """Dense embedding cassette."""

    model_config = _FROZEN_IGNORE

    model: str
    text_hash: str
    vector: list[float]
    text_preview: str


class SparseCassette(BaseModel):
    """Sparse embedding cassette (Qdrant/bm25)."""

    model_config = _FROZEN_IGNORE

    model: str
    text_hash: str
    indices: list[int]
    values: list[float]
    text_preview: str


# Read-side envelope models mirror the on-disk JSON. `extra="ignore"` is the
# P12 lenient-versioning hook: a future writer adding `logprobs` or
# `trace_id` deserializes cleanly under the current reader.


class _LlmKey(BaseModel):
    model_config = _IGNORE
    template_sha: str
    model: str
    prompt_hash: str


class _LlmResponse(BaseModel):
    model_config = _IGNORE
    text: str
    stop_reason: str = "stop"
    cost_usd: float = 0.0
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None


class _LlmDebug(BaseModel):
    model_config = _IGNORE
    prompt_preview: str = ""
    system_preview: str = ""
    tools_present: list[str] = []
    response_format_present: bool = False


class _LlmEnvelope(BaseModel):
    model_config = _IGNORE
    schema_version: str
    key: _LlmKey
    response: _LlmResponse
    request_debug: _LlmDebug = _LlmDebug()


class _EmbedKey(BaseModel):
    model_config = _IGNORE
    model: str
    text_hash: str


class _EmbedResponse(BaseModel):
    model_config = _IGNORE
    vector: list[float] | None = None  # dense
    indices: list[int] | None = None  # sparse
    values: list[float] | None = None  # sparse


class _EmbedEnvelope(BaseModel):
    model_config = _IGNORE
    schema_version: str
    key: _EmbedKey
    response: _EmbedResponse


_LLM_ADAPTER: TypeAdapter[_LlmEnvelope] = TypeAdapter(_LlmEnvelope)
_EMBED_ADAPTER: TypeAdapter[_EmbedEnvelope] = TypeAdapter(_EmbedEnvelope)


def write_llm_cassette(cas: LlmCassette, out_dir: Path, *, stage: str) -> Path:
    """Write `cas` to `<out_dir>/<stage>__<slug(model)>__<prompt_hash>.json`."""
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{stage}__{_slugify_model(cas.model)}__{cas.prompt_hash}.json"
    path = out_dir / fname
    path.write_text(
        json.dumps(
            {
                "schema_version": CASSETTE_SCHEMA_VERSION,
                "key": {
                    "template_sha": cas.template_sha,
                    "model": cas.model,
                    "prompt_hash": cas.prompt_hash,
                },
                "response": {
                    "text": cas.text,
                    "stop_reason": cas.stop_reason,
                    "cost_usd": cas.cost_usd,
                    "cache_read_tokens": cas.cache_read_tokens,
                    "cache_creation_tokens": cas.cache_creation_tokens,
                },
                "request_debug": {
                    "prompt_preview": cas.prompt_preview,
                    "system_preview": cas.system_preview,
                    "tools_present": cas.tools_present,
                    "response_format_present": cas.response_format_present,
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return path


def write_embedding_cassette(cas: EmbeddingCassette, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"embed__{_slugify_model(cas.model)}__{cas.text_hash}.json"
    path = out_dir / fname
    path.write_text(
        json.dumps(
            {
                "schema_version": CASSETTE_SCHEMA_VERSION,
                "key": {"model": cas.model, "text_hash": cas.text_hash},
                "response": {"vector": cas.vector},
                "request_debug": {
                    "text_preview": cas.text_preview,
                    "vector_dim": len(cas.vector),
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return path


def write_sparse_cassette(cas: SparseCassette, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"embed__{_slugify_model(cas.model)}__{cas.text_hash}.json"
    path = out_dir / fname
    path.write_text(
        json.dumps(
            {
                "schema_version": CASSETTE_SCHEMA_VERSION,
                "key": {"model": cas.model, "text_hash": cas.text_hash},
                "response": {"indices": cas.indices, "values": cas.values},
                "request_debug": {
                    "text_preview": cas.text_preview,
                    "nnz": len(cas.indices),
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return path


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        msg = f"cassette {path} is not valid JSON: {exc}"
        raise CassetteFormatError(msg) from exc
    if not isinstance(raw, dict):
        msg = f"cassette {path} top-level must be an object"
        raise CassetteFormatError(msg)
    return raw  # pyright: ignore[reportReturnType]


def load_llm_cassettes(
    scope_dir: Path,
) -> Mapping[tuple[str, str, str], LlmCassette]:
    """Load every `<stage>__*.json` (non-`embed__`) under `scope_dir`.

    Validates each file via `TypeAdapter[_LlmEnvelope]` (P16): a cassette
    with the wrong type for `cost_usd` raises `CassetteFormatError` with
    the path, not a confusing `TypeError` later at use site.
    """
    out: dict[tuple[str, str, str], LlmCassette] = {}
    if not scope_dir.exists():
        return out
    for path in sorted(scope_dir.glob("*.json")):
        if path.name.startswith("embed__"):
            continue
        raw = _read_json_object(path)
        _check_schema_version(raw.get("schema_version"), path=path)
        try:
            env = _LLM_ADAPTER.validate_python(raw)
        except ValidationError as exc:
            msg = f"cassette {path} failed schema validation: {exc}"
            raise CassetteFormatError(msg) from exc
        cas = LlmCassette(
            template_sha=env.key.template_sha,
            model=env.key.model,
            prompt_hash=env.key.prompt_hash,
            text=env.response.text,
            stop_reason=env.response.stop_reason,
            cost_usd=env.response.cost_usd,
            cache_read_tokens=env.response.cache_read_tokens,
            cache_creation_tokens=env.response.cache_creation_tokens,
            prompt_preview=env.request_debug.prompt_preview,
            system_preview=env.request_debug.system_preview,
            tools_present=env.request_debug.tools_present,
            response_format_present=env.request_debug.response_format_present,
        )
        key = (cas.template_sha, cas.model, cas.prompt_hash)
        if key in out:
            msg = f"duplicate cassette key {key!r} in {scope_dir}"
            raise DuplicateCassetteError(msg)
        out[key] = cas
    return out


def load_embedding_cassettes(
    scope_dir: Path,
) -> tuple[
    Mapping[tuple[str, str], list[float]],
    Mapping[tuple[str, str], tuple[list[int], list[float]]],
]:
    """Load every `embed__*.json` under `scope_dir`; split sparse vs dense via response shape."""
    dense: dict[tuple[str, str], list[float]] = {}
    sparse: dict[tuple[str, str], tuple[list[int], list[float]]] = {}
    if not scope_dir.exists():
        return dense, sparse
    for path in sorted(scope_dir.glob("embed__*.json")):
        raw = _read_json_object(path)
        _check_schema_version(raw.get("schema_version"), path=path)
        try:
            env = _EMBED_ADAPTER.validate_python(raw)
        except ValidationError as exc:
            msg = f"cassette {path} failed schema validation: {exc}"
            raise CassetteFormatError(msg) from exc
        model = env.key.model
        text_hash = env.key.text_hash
        if env.response.indices is not None and env.response.values is not None:
            key_s = (model, text_hash)
            if key_s in sparse:
                msg = f"duplicate sparse cassette key {key_s!r} in {scope_dir}"
                raise DuplicateCassetteError(msg)
            sparse[key_s] = (env.response.indices, env.response.values)
        elif env.response.vector is not None:
            key_d = (model, text_hash)
            if key_d in dense:
                msg = f"duplicate dense cassette key {key_d!r} in {scope_dir}"
                raise DuplicateCassetteError(msg)
            dense[key_d] = env.response.vector
        else:
            msg = f"embed cassette {path} response has neither vector nor (indices, values)"
            raise CassetteFormatError(msg)
    return dense, sparse
```

- [x] **Step 8: Run cassette tests**

Run: `uv run pytest tests/test_cassettes.py -v`
Expected: all tests pass.

### 1C — Widen `FakeLLMClient.canned` key to 3-tuple, strict no-fallback lookup

- [x] **Step 9: Add a failing test for the new 3-tuple lookup**

Add to `tests/test_cassettes.py`:

```python
from slopmortem.llm.fake import FakeLLMClient, FakeResponse, NoCannedResponseError


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
```

The project's `pyproject.toml` sets `asyncio_mode = "auto"`; bare `async def test_…` functions are collected automatically. No `anyio` marker, no `anyio_backend` fixture.

- [x] **Step 10: Run the test to verify it fails**

Run: `uv run pytest tests/test_cassettes.py::test_fake_llm_client_keys_on_three_tuple tests/test_cassettes.py::test_fake_llm_client_strict_no_wildcard_fallback -v`
Expected: FAIL — `FakeLLMClient.canned` is currently typed `Mapping[tuple[str, str], ...]`.

- [x] **Step 11: Widen `FakeLLMClient.canned` to 3-tuple in `slopmortem/llm/fake.py`**

Modify `slopmortem/llm/fake.py`:

- Change the `canned` field type to `Mapping[tuple[str, str, str], FakeResponse | CompletionResult]`.
- After deriving `eff_model` and `template_sha`, also derive `prompt_hash`: read `extra_body["prompt_hash"]` if present; otherwise compute it inline using `slopmortem.llm.cassettes.llm_cassette_key(prompt=prompt, system=system, template_sha=template_sha, model=eff_model)[2]` (this avoids duplicating the hashing rule).
- Build `key = (template_sha, eff_model, prompt_hash)`. Strict lookup: if `key not in canned`, raise `NoCannedResponseError(f"no canned response for key={key!r}; recorded keys: {sorted(canned)}")`. **Do not fall back to a 2-tuple.**
- Update `_Call.template_sha` siblings to also store `prompt_hash` for assertions in tests.
- Update the class docstring to reflect the new 3-tuple key.
- Top-level import: `from slopmortem.llm.cassettes import llm_cassette_key`. **No lazy import** — `slopmortem.llm.cassettes` is a sibling module with no `evals` dependency (P17 fix), so the cycle the lazy import was guarding against does not exist.

The full method body:

```python
async def complete(
    self,
    prompt: str,
    *,
    system: str | None = None,
    tools: list[Any] | None = None,
    model: str | None = None,
    cache: bool = False,
    response_format: dict[str, Any] | None = None,
    extra_body: dict[str, Any] | None = None,
) -> CompletionResult:
    eff_model = model or self.default_model
    template_sha: str | None = None
    if extra_body and "prompt_template_sha" in extra_body:
        template_sha = str(extra_body["prompt_template_sha"])
    if template_sha is None:
        msg = (
            "FakeLLMClient requires extra_body['prompt_template_sha']; "
            f"none supplied for model {eff_model!r}"
        )
        raise NoCannedResponseError(msg)
    # Compute prompt_hash from prompt+system; allow override via extra_body
    # so tests that want to pin a specific hash can do so.
    prompt_hash: str
    if extra_body and "prompt_hash" in extra_body:
        prompt_hash = str(extra_body["prompt_hash"])
    else:
        _, _, prompt_hash = llm_cassette_key(
            prompt=prompt, system=system, template_sha=template_sha, model=eff_model,
        )
    self.calls.append(
        _Call(
            prompt=prompt,
            model=eff_model,
            template_sha=template_sha,
            prompt_hash=prompt_hash,
            system=system,
            tools=tools,
            cache=cache,
            response_format=response_format,
            extra_body=extra_body,
        )
    )
    key = (template_sha, eff_model, prompt_hash)
    if key not in self.canned:
        msg = (
            f"no canned response for key={key!r}; recorded keys: {sorted(self.canned)}"
        )
        raise NoCannedResponseError(msg)
    item = self.canned[key]
    if isinstance(item, CompletionResult):
        return item
    return item.to_completion()
```

Add `prompt_hash: str | None` to `_Call` (default `None`).

- [x] **Step 12: Run the FakeLLMClient tests**

Run: `uv run pytest tests/test_cassettes.py -k fake_llm_client -v`
Expected: PASS.

### 1D — Migrate every existing 2-tuple call site to 3-tuple

Approach: introduce a tiny per-test helper `_three(template_name, model, prompt)` that builds `(prompt_template_sha(template_name), model, llm_cassette_key(...)[2])`. Then update each `_canned()`/`_build_canned()` to use that helper. The simpler path — wildcard `prompt_hash="*"` — would re-introduce the strictness violation (B4) we just fixed, so it's not allowed.

For tests where the same canned response should match every prompt (e.g. `tests/test_ingest_orchestration.py:206-223` runs the same template for every fan-out), expand the canned dict at construction time by enumerating every prompt the run will make. The test already knows the fixture inputs, so the rendered prompt is computable at fixture-build time.

- [x] **Step 13: Add a small shared helper in tests/conftest.py**

Add to `tests/conftest.py` (verify it doesn't exist first; if a `conftest.py` exists, append; otherwise create):

```python
from __future__ import annotations

from slopmortem.llm.cassettes import llm_cassette_key
from slopmortem.llm.prompts import prompt_template_sha


def llm_canned_key(
    template_name: str,
    *,
    model: str,
    prompt: str,
    system: str | None = None,
) -> tuple[str, str, str]:
    """Build the 3-tuple key the same way `FakeLLMClient` does internally."""
    tsha = prompt_template_sha(template_name)
    return llm_cassette_key(prompt=prompt, system=system, template_sha=tsha, model=model)
```

Note: we expose this as a regular Python helper rather than a pytest fixture so test modules can call it from `_canned()` builders that aren't fixtures themselves.

- [x] **Step 14: Update each existing `_canned()`/`_build_canned()` site**

Per file, replace the 2-tuple key with a 3-tuple. The pattern is the same in each:

Before:
```python
(prompt_template_sha("facet_extract"), _FACET_MODEL): FakeResponse(text=...)
```

After (when one prompt is rendered):
```python
from tests.conftest import llm_canned_key
prompt = render_prompt("facet_extract", description=ctx.description)  # or whatever vars the stage passes
llm_canned_key("facet_extract", model=_FACET_MODEL, prompt=prompt): FakeResponse(text=...)
```

For tests that fan out (synthesize × N candidates), iterate over the candidates and build one entry per rendered prompt. Each test file lists the prompt-rendering inputs; reuse them.

Per file:

- `tests/test_pipeline_e2e.py:138-156` — `_build_canned` produces the canned dict for facet/rerank/synthesize. Rewrite to render each prompt for each candidate using `slopmortem.llm.prompts.render_prompt` with the same template-vars the stage uses (see `slopmortem/stages/{facet_extract,llm_rerank,synthesize}.py`). For synthesize, iterate over the top-N candidates.
- `tests/test_observe_redaction.py:152-166` — same shape as `test_pipeline_e2e`.
- `tests/test_ingest_idempotency.py:40-50` — facet + summarize per ingest item.
- `tests/test_ingest_dry_run.py:38-43` — same.
- `tests/test_ingest_orchestration.py:60-90, 259-271` — facet + summarize across all sources; fixture `_canned_for_run` already lists the expected sources, iterate and render. **Two N-prompt fan-out tests in this file** (P8): `test_ingest_bounded_fan_out_concurrency` (line 194, n=30) and `test_ingest_cache_read_ratio_warning` (line 291, n=6) build one canned `FakeResponse` per call but distinct prompts render per source. Expand `_canned_for_run` (or per-test `canned=…` literal) to take the source list and emit one entry per rendered `(template_sha, model, prompt_hash)` tuple — otherwise only `source[0]` matches and the rest raise `NoCannedResponseError`.
- `tests/stages/test_synthesize.py:96-138` — first test (lines 96-120) wraps a single candidate, one rendered prompt. **Second test `test_synthesize_all_warms_cache_before_gather` (line 122, n=3 candidates)** shares one canned response across 3 distinct rendered prompts (current code's comment at line 126 acknowledges this works only because the lookup is non-strict on prompt). After the 3-tuple migration, expand to one entry per rendered prompt.
- `tests/stages/test_llm_rerank.py:89-134` — one rendered prompt per test.
- `tests/stages/test_facet_extract.py:30-74` — one rendered prompt per test.

For each file, after editing, run only that file's tests to verify the rewrite (parallel-safe).

- [x] **Step 15: Run each migrated file individually**

Run: `uv run pytest tests/stages/test_facet_extract.py tests/stages/test_llm_rerank.py tests/stages/test_synthesize.py tests/test_ingest_idempotency.py tests/test_ingest_dry_run.py tests/test_ingest_orchestration.py tests/test_observe_redaction.py tests/test_pipeline_e2e.py -v`
Expected: all green.

- [x] **Step 16: Run the full test suite to verify no other site broke**

Run: `just test`
Expected: green. Any red site we missed has the symptom `Mapping[tuple[str, str], ...]` vs `Mapping[tuple[str, str, str], ...]` — fix in place.

### 1E — `FakeEmbeddingClient` optional canned dict

- [x] **Step 17: Write failing tests for `FakeEmbeddingClient` strict-canned + sha-fallthrough**

Add to `tests/test_cassettes.py`:

```python
from slopmortem.evals.cassettes import NoCannedEmbeddingError
from slopmortem.llm.fake_embeddings import FakeEmbeddingClient


async def test_fake_embedding_client_strict_when_canned_supplied() -> None:
    text_hash = hashlib.sha256(b"hello").hexdigest()[:16]
    canned = {(text_hash, "text-embedding-3-small"): [0.1] * 1536}
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
```

- [x] **Step 18: Run the tests**

Run: `uv run pytest tests/test_cassettes.py -k fake_embedding -v`
Expected: FAIL.

- [x] **Step 19: Add optional `canned` parameter to `FakeEmbeddingClient`**

Modify `slopmortem/llm/fake_embeddings.py`:

- Add optional constructor param `canned: Mapping[tuple[str, str], list[float]] | None = None`. Note the key shape is `(text_hash, model)` to match cassette keys (G9 from spec).
- In `embed()`, if `self._canned is not None`: for each text, compute `embed_cassette_key(text=text, model=eff_model)`; if missing, raise `NoCannedEmbeddingError(f"no canned embedding for key={key!r}")`. Return `EmbeddingResult(vectors=[...], n_tokens=0, cost_usd=0.0)` regardless of `cost_per_call` (cassette replay is free by definition; G8).
- If `self._canned is None`: keep today's `_sha_vector` behavior verbatim.

Watch: `embed_cassette_key` returns `(model, text_hash)`; the canned key shape we expose to callers is `(text_hash, model)` per the spec's resolved-question 3. Pick one and stick to it. For consistency with `embed_cassette_key`, **use `(model, text_hash)` everywhere** — update the test in step 17 accordingly:

```python
canned = {("text-embedding-3-small", text_hash): [0.1] * 1536}
```

Implementation:

```python
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from slopmortem.evals.cassettes import NoCannedEmbeddingError
from slopmortem.llm.cassettes import embed_cassette_key
from slopmortem.llm.embedding_client import EmbeddingResult
from slopmortem.llm.openai_embeddings import EMBED_DIMS

if TYPE_CHECKING:
    from collections.abc import Mapping


class FakeEmbeddingClient:
    def __init__(
        self,
        *,
        model: str,
        cost_per_call: float = 0.0,
        canned: Mapping[tuple[str, str], list[float]] | None = None,
        calls: list[_EmbedCall] | None = None,
    ) -> None:
        if model not in EMBED_DIMS:
            msg = f"unknown embed model {model!r}; add it to EMBED_DIMS"
            raise ValueError(msg)
        self.model = model
        self.cost_per_call = cost_per_call
        self._canned = canned
        self.calls: list[_EmbedCall] = calls if calls is not None else []

    @property
    def dim(self) -> int:
        return EMBED_DIMS[self.model]

    async def embed(self, texts: list[str], *, model: str | None = None) -> EmbeddingResult:
        eff_model = model or self.model
        self.calls.append(_EmbedCall(texts=list(texts), model=eff_model))
        if self._canned is not None:
            vectors: list[list[float]] = []
            for text in texts:
                key = embed_cassette_key(text=text, model=eff_model)
                if key not in self._canned:
                    msg = (
                        f"no canned embedding for key={key!r}; "
                        f"recorded keys: {sorted(self._canned)}"
                    )
                    raise NoCannedEmbeddingError(msg)
                vectors.append(list(self._canned[key]))
            return EmbeddingResult(vectors=vectors, n_tokens=0, cost_usd=0.0)
        # sha fallback (today's behavior)
        dim = EMBED_DIMS[eff_model] if model is not None else self.dim
        vectors = [_sha_vector(t, dim) for t in texts]
        return EmbeddingResult(
            vectors=vectors, n_tokens=0, cost_usd=self.cost_per_call * len(texts),
        )
```

- [x] **Step 20: Run the embedding tests**

Run: `uv run pytest tests/test_cassettes.py -k fake_embedding -v`
Expected: PASS.

### 1F — Recording wrappers

- [x] **Step 21: Write failing tests for `RecordingLLMClient`, `RecordingEmbeddingClient`, `RecordingSparseEncoder`**

Create `tests/test_recording.py`:

```python
"""Tests for RecordingLLMClient, RecordingEmbeddingClient, RecordingSparseEncoder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from slopmortem.evals.cassettes import (
    RecordingBudgetExceededError,
    load_embedding_cassettes,
    load_llm_cassettes,
)
from slopmortem.evals.recording import (
    RecordingEmbeddingClient,
    RecordingLLMClient,
    RecordingSparseEncoder,
)
from slopmortem.llm.cassettes import embed_cassette_key, llm_cassette_key
from slopmortem.llm.client import CompletionResult
from slopmortem.llm.embedding_client import EmbeddingResult


class _FakeInnerLLM:
    def __init__(self, *, text: str = "ok", cost_usd: float = 0.10) -> None:
        self.text = text
        self.cost_usd = cost_usd
        self.calls: int = 0
        self.raise_on_call: int | None = None

    async def complete(self, prompt: str, *, system=None, tools=None, model=None,
                       cache=False, response_format=None, extra_body=None):
        self.calls += 1
        if self.raise_on_call is not None and self.calls == self.raise_on_call:
            raise RuntimeError("simulated inner failure")
        return CompletionResult(
            text=self.text, stop_reason="stop", cost_usd=self.cost_usd,
            cache_read_tokens=0, cache_creation_tokens=0,
        )


async def test_recording_llm_writes_cassette_on_success(tmp_path: Path) -> None:
    inner = _FakeInnerLLM(text="hello")
    rec = RecordingLLMClient(
        inner=inner, out_dir=tmp_path,
        stage_registry={"tsha-abc": "facet_extract"},
    )
    extra = {"prompt_template_sha": "tsha-abc"}
    result = await rec.complete("the prompt", model="anthropic/claude-haiku-4.5", extra_body=extra)
    assert result.text == "hello"
    files = sorted(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert files[0].name.startswith("facet_extract__anthropic_claude-haiku-4.5__")


async def test_recording_llm_falls_back_to_template_sha_prefix_when_unregistered(tmp_path: Path) -> None:
    """Unknown template_sha → first-8-hex prefix; ensures stage-agnostic callers still get a filename."""
    inner = _FakeInnerLLM(text="ok")
    rec = RecordingLLMClient(inner=inner, out_dir=tmp_path)  # no registry
    extra = {"prompt_template_sha": "abc12345deadbeef"}
    _ = await rec.complete("p", model="m", extra_body=extra)
    files = sorted(tmp_path.glob("*.json"))
    assert len(files) == 1
    assert files[0].name.startswith("abc12345__m__")


async def test_recording_llm_skips_cassette_on_inner_error(tmp_path: Path) -> None:
    inner = _FakeInnerLLM()
    inner.raise_on_call = 1
    rec = RecordingLLMClient(inner=inner, out_dir=tmp_path)
    with pytest.raises(RuntimeError):
        _ = await rec.complete("the prompt", model="m", extra_body={"prompt_template_sha": "t"})
    assert list(tmp_path.glob("*.json")) == []


async def test_recording_llm_cost_ceiling_post_call_raise_after_cassette_written(tmp_path: Path) -> None:
    """Soft cap: post-call raise after settle. Cassette IS written for the call that pushed over."""
    inner = _FakeInnerLLM(cost_usd=0.40)
    rec = RecordingLLMClient(inner=inner, out_dir=tmp_path, max_cost_usd=0.99)
    extra = {"prompt_template_sha": "t"}
    # Calls 1 and 2 settle under the cap.
    _ = await rec.complete("a", model="m", extra_body={**extra, "prompt_hash": "0" * 16})
    _ = await rec.complete("b", model="m", extra_body={**extra, "prompt_hash": "1" * 16})
    # Call 3: pre-check 0.80 >= 0.99 False → proceeds → settle to 1.20 → cassette written
    # → post-check 1.20 > 0.99 True → raises. Cassette `c` is on disk.
    with pytest.raises(RecordingBudgetExceededError) as exc_info:
        _ = await rec.complete("c", model="m", extra_body={**extra, "prompt_hash": "2" * 16})
    assert exc_info.value.spent == pytest.approx(1.20)
    assert exc_info.value.limit == pytest.approx(0.99)
    assert inner.calls == 3
    assert len(list(tmp_path.glob("*.json"))) == 3


async def test_recording_llm_cost_ceiling_pre_call_refusal_when_already_over(tmp_path: Path) -> None:
    """After post-call raise, a subsequent call hits the pre-call refusal — no cassette, inner not called."""
    inner = _FakeInnerLLM(cost_usd=0.40)
    rec = RecordingLLMClient(inner=inner, out_dir=tmp_path, max_cost_usd=0.99)
    extra = {"prompt_template_sha": "t"}
    _ = await rec.complete("a", model="m", extra_body={**extra, "prompt_hash": "0" * 16})
    _ = await rec.complete("b", model="m", extra_body={**extra, "prompt_hash": "1" * 16})
    with pytest.raises(RecordingBudgetExceededError):
        _ = await rec.complete("c", model="m", extra_body={**extra, "prompt_hash": "2" * 16})
    # Now spent=1.20, well over cap. Next call refuses pre-flight; inner.calls and cassette count unchanged.
    pre_calls = inner.calls
    pre_files = len(list(tmp_path.glob("*.json")))
    with pytest.raises(RecordingBudgetExceededError):
        _ = await rec.complete("d", model="m", extra_body={**extra, "prompt_hash": "3" * 16})
    assert inner.calls == pre_calls
    assert len(list(tmp_path.glob("*.json"))) == pre_files


class _FakeInnerEmbed:
    model = "text-embedding-3-small"
    dim = 1536

    async def embed(self, texts, *, model=None):
        # Return a vector that's distinct per input.
        return EmbeddingResult(
            vectors=[[float(i)] * 1536 for i, _ in enumerate(texts)],
            n_tokens=len(texts),
            cost_usd=0.0,
        )


async def test_recording_embed_splits_batch_into_per_text_cassettes(tmp_path: Path) -> None:
    inner = _FakeInnerEmbed()
    rec = RecordingEmbeddingClient(inner=inner, out_dir=tmp_path)
    result = await rec.embed(["hello", "world", "hello"])  # repeated text → same cassette
    assert len(result.vectors) == 3
    files = sorted(tmp_path.glob("embed__*.json"))
    # Two unique texts → two cassettes; "hello" overwrites itself idempotently.
    assert len(files) == 2


class _FakeInnerSparse:
    @staticmethod
    def encode(text: str) -> dict[int, float]:
        return {1: 0.5, 2: 0.25}


async def test_recording_sparse_writes_qdrant_bm25_cassette(tmp_path: Path) -> None:
    rec = RecordingSparseEncoder(inner=_FakeInnerSparse.encode, out_dir=tmp_path)
    out = rec("hello")
    assert out == {1: 0.5, 2: 0.25}
    dense, sparse = load_embedding_cassettes(tmp_path)
    assert dense == {}
    [(k, (idx, vals))] = list(sparse.items())
    assert k[0] == "Qdrant/bm25"
    assert sorted(zip(idx, vals)) == [(1, 0.5), (2, 0.25)]
```

- [x] **Step 22: Run the failing tests**

Run: `uv run pytest tests/test_recording.py -v`
Expected: import errors / fail.

- [x] **Step 23: Implement `slopmortem/evals/recording.py`**

```python
"""Recording wrappers: forward to a real client, write a cassette on success.

Lives under `slopmortem/evals/` rather than `slopmortem/llm/` so the
production LLM surface stays free of test infrastructure and the import
direction stays one-way (`evals → llm`, never `llm → evals`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from slopmortem.evals.cassettes import (
    EmbeddingCassette,
    LlmCassette,
    RecordingBudgetExceededError,
    SparseCassette,
    write_embedding_cassette,
    write_llm_cassette,
    write_sparse_cassette,
)
from slopmortem.llm.cassettes import embed_cassette_key, llm_cassette_key
from slopmortem.llm.client import CompletionResult
from slopmortem.llm.embedding_client import EmbeddingResult

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from slopmortem.llm.client import LLMClient
    from slopmortem.llm.embedding_client import EmbeddingClient


_PREVIEW_CHARS_PROMPT = 500
_PREVIEW_CHARS_TEXT = 200


class RecordingLLMClient:
    """Wrap a real `LLMClient`; write one LLM cassette per `complete()` call.

    Stage and model are derived per-call (from `extra_body['prompt_template_sha']`
    and the `model=` arg) so a single wrapper can record across heterogeneous
    callers (e.g. `ingest()` calls `summarize`, `facet_extract`, and the
    entity-resolution tiebreaker through one `LLMClient`).

    Cost ceiling is **soft**: pre-call refusal at `spent >= cap` and post-call
    raise at `spent > cap` after the cassette is written. A single oversized
    call therefore overshoots by at most one call's `cost_usd`. Bounded by
    `cap + max_single_call_cost_usd`. See spec §F20 / plan §P4.
    """

    def __init__(
        self,
        *,
        inner: LLMClient,
        out_dir: Path,
        max_cost_usd: float | None = None,
        stage_registry: Mapping[str, str] | None = None,
    ) -> None:
        self._inner = inner
        self._out_dir = out_dir
        self._max_cost_usd = max_cost_usd
        # Map template_sha → human-readable filename prefix. Misses fall back
        # to template_sha[:8].
        self._stage_registry = dict(stage_registry or {})
        self._spent_usd = 0.0

    async def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        tools: list[Any] | None = None,
        model: str | None = None,
        cache: bool = False,
        response_format: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> CompletionResult:
        # Pre-call refusal: already at/over the cap.
        if self._max_cost_usd is not None and self._spent_usd >= self._max_cost_usd:
            raise RecordingBudgetExceededError(
                spent=self._spent_usd, limit=self._max_cost_usd,
            )
        result = await self._inner.complete(
            prompt,
            system=system,
            tools=tools,
            model=model,
            cache=cache,
            response_format=response_format,
            extra_body=extra_body,
        )
        # Settle cost first so the post-call check sees this call's spend.
        self._spent_usd += result.cost_usd
        if model is None:
            msg = "RecordingLLMClient requires a model on every complete() call"
            raise ValueError(msg)
        template_sha = ""
        if extra_body and "prompt_template_sha" in extra_body:
            template_sha = str(extra_body["prompt_template_sha"])
        # Stage prefix: registry hit → human name; miss → first 8 chars of template_sha.
        stage = self._stage_registry.get(
            template_sha,
            template_sha[:8] if template_sha else "unknown",
        )
        # Allow override (tests pin a known prompt_hash); otherwise compute.
        if extra_body and "prompt_hash" in extra_body:
            prompt_hash = str(extra_body["prompt_hash"])
        else:
            _, _, prompt_hash = llm_cassette_key(
                prompt=prompt, system=system, template_sha=template_sha, model=model,
            )
        cas = LlmCassette(
            template_sha=template_sha,
            model=model,
            prompt_hash=prompt_hash,
            text=result.text,
            stop_reason=result.stop_reason,
            cost_usd=result.cost_usd,
            cache_read_tokens=result.cache_read_tokens,
            cache_creation_tokens=result.cache_creation_tokens,
            prompt_preview=prompt[:_PREVIEW_CHARS_PROMPT],
            system_preview=(system or "")[:_PREVIEW_CHARS_PROMPT],
            tools_present=[getattr(t, "name", str(t)) for t in (tools or [])],
            response_format_present=response_format is not None,
        )
        write_llm_cassette(cas, self._out_dir, stage=stage)
        # Post-call raise (soft cap): this call pushed us past the limit.
        # Cassette is already on disk; the operator paid for the data, keep it.
        if self._max_cost_usd is not None and self._spent_usd > self._max_cost_usd:
            raise RecordingBudgetExceededError(
                spent=self._spent_usd, limit=self._max_cost_usd,
            )
        return result


class RecordingEmbeddingClient:
    """Wrap a real `EmbeddingClient`; write one cassette per text per `embed()`."""

    def __init__(self, *, inner: EmbeddingClient, out_dir: Path) -> None:
        self._inner = inner
        self._out_dir = out_dir

    @property
    def model(self) -> str:
        return self._inner.model  # pyright: ignore[reportAttributeAccessIssue]

    async def embed(self, texts: list[str], *, model: str | None = None) -> EmbeddingResult:
        result = await self._inner.embed(texts, model=model)
        eff_model = model or self.model
        for text, vector in zip(texts, result.vectors, strict=True):
            _, text_hash = embed_cassette_key(text=text, model=eff_model)
            cas = EmbeddingCassette(
                model=eff_model,
                text_hash=text_hash,
                vector=list(vector),
                text_preview=text[:_PREVIEW_CHARS_TEXT],
            )
            write_embedding_cassette(cas, self._out_dir)
        return result


class RecordingSparseEncoder:
    """Wrap a sparse-encoder callable; write one cassette per `__call__`."""

    def __init__(
        self,
        *,
        inner: Callable[[str], dict[int, float]],
        out_dir: Path,
        model: str = "Qdrant/bm25",
    ) -> None:
        self._inner = inner
        self._out_dir = out_dir
        self._model = model

    def __call__(self, text: str) -> dict[int, float]:
        result = self._inner(text)
        _, text_hash = embed_cassette_key(text=text, model=self._model)
        items = sorted(result.items())
        cas = SparseCassette(
            model=self._model,
            text_hash=text_hash,
            indices=[k for k, _ in items],
            values=[v for _, v in items],
            text_preview=text[:_PREVIEW_CHARS_TEXT],
        )
        write_sparse_cassette(cas, self._out_dir)
        return result
```

- [x] **Step 24: Run the recording tests**

Run: `uv run pytest tests/test_recording.py -v`
Expected: PASS.

- [x] **Step 25: Run the entire suite + typecheck**

Run: `just test && just typecheck`
Expected: green. **Note:** the helper `llm_canned_key` was moved to the **rootdir** `conftest.py` (not `tests/conftest.py`) to avoid shadowing the rootdir conftest's `_scrub_body` (which `tests/llm/test_secrets_scrub.py` imports). All 13 test files import via `from conftest import llm_canned_key`. `extraPaths = ["."]` in `pyproject.toml`'s basedpyright config makes that resolvable. `tests/conftest.py` does not exist.

---

## Task 2: Corpus fixture machinery

Owner: one subagent.

**Files:**
- Create: `slopmortem/evals/corpus_fixture.py`
- Create: `tests/evals/__init__.py` (if missing)
- Create: `tests/evals/test_corpus_fixture.py`
- Create: `tests/fixtures/corpus_fixture_inputs.yml`
- Create: `tests/fixtures/corpus_fixture_bodies/` (directory; one `.md` file per seed entry, populated during commit 5 by the operator)

### 2A — Hand-curated seed-input list

The corpus fixture is regenerable; the YAML index + sibling body files are the source of truth for what gets ingested. Body text lives in `tests/fixtures/corpus_fixture_bodies/<canonical_id>.md` rather than inline in the YAML — escaping landmines for code blocks/quotes are unavoidable in YAML literal blocks, sibling text files diff cleanly, and individual body edits don't churn the index.

- [x] **Step 1: Create `tests/fixtures/corpus_fixture_inputs.yml` with ~30 entries**

The schema is bespoke for cassette regen — `_SeedInputSource` (Task 5,
`slopmortem/evals/corpus_recorder.py`) reads it and emits `RawEntry` records.
Operator hand-curates ~30 entries with mixed sectors so the eval's facet
retrieval has coverage across `(sector, business_model, customer_type,
geography, monetization)`. Initial content (3 entries shown — operator grows
to ~30 during commit 5; commit message references each ingested document):

```yaml
- canonical_id: solyndra
  source: seed
  source_id: solyndra
  body_path: corpus_fixture_bodies/solyndra.md
  url: https://en.wikipedia.org/wiki/Solyndra
  fetched_at: "2026-04-29T00:00:00Z"
- canonical_id: theranos
  source: seed
  source_id: theranos
  body_path: corpus_fixture_bodies/theranos.md
  url: https://en.wikipedia.org/wiki/Theranos
  fetched_at: "2026-04-29T00:00:00Z"
- canonical_id: webvan
  source: seed
  source_id: webvan
  body_path: corpus_fixture_bodies/webvan.md
  url: https://en.wikipedia.org/wiki/Webvan
  fetched_at: "2026-04-29T00:00:00Z"
```

`url:` carries the source attribution (used downstream by
`all_sources_in_allowed_domains`); the body bytes come from `body_path` only.
Operator copies cleaned text from each source into the corresponding `.md`
file once, then never re-fetches — this is what makes corpus regen
deterministic across runs.

Note on URL choice: Wikipedia is the safe default (`wikipedia.org` is in
`_FIXED_HOST_ALLOWLIST` per `slopmortem/stages/synthesize.py`). Avoid HN/HN-
comment URLs (IDs age out); YC's allowlist excerpts break under recency
filters.

### 2B — `dump_collection_to_jsonl`, `restore_jsonl_to_collection`, `compute_fixture_sha256`

- [x] **Step 2: Write failing round-trip integration tests**

Create `tests/evals/__init__.py` (empty if missing).

Create `tests/evals/test_corpus_fixture.py`:

```python
"""Round-trip tests for corpus_fixture dump/restore + SHA stability."""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

import pytest
from qdrant_client import AsyncQdrantClient

from slopmortem.evals.corpus_fixture import (
    compute_fixture_sha256,
    dump_collection_to_jsonl,
    restore_jsonl_to_collection,
)

pytestmark = pytest.mark.requires_qdrant


@pytest.fixture
def qdrant_url() -> str:
    return os.environ.get("QDRANT_URL", "http://localhost:6333")


@pytest.fixture
async def client(qdrant_url: str):
    c = AsyncQdrantClient(url=qdrant_url)
    yield c
    await c.close()


@pytest.fixture
def collection_name() -> str:
    return f"slopmortem_test_{os.getpid()}_{uuid.uuid4().hex}"


async def test_round_trip_preserves_query_results(
    client: AsyncQdrantClient, collection_name: str, tmp_path: Path
) -> None:
    # Bootstrap a small collection (use slopmortem.corpus.qdrant_store.ensure_collection).
    from slopmortem.corpus.qdrant_store import ensure_collection

    await ensure_collection(client, collection_name, dim=8)
    # Upsert a few synthetic points (use small dim so the test is fast).
    # ... (operator wires concrete points; tests run only under requires_qdrant)
    fixture_path = tmp_path / "out.jsonl"
    await dump_collection_to_jsonl(client, collection_name, fixture_path)
    assert fixture_path.exists()
    sha_a = compute_fixture_sha256(fixture_path)
    assert len(sha_a) == 64

    # Restore into a fresh collection and re-dump.
    fresh = collection_name + "_fresh"
    await ensure_collection(client, fresh, dim=8)
    try:
        await restore_jsonl_to_collection(client, fresh, fixture_path)
        # Dump and compare line-by-line (sorted) — payloads + vectors should match.
        out_b = tmp_path / "out_b.jsonl"
        await dump_collection_to_jsonl(client, fresh, out_b)
        assert sorted(fixture_path.read_text().splitlines()) == sorted(out_b.read_text().splitlines())
    finally:
        await client.delete_collection(fresh)
        await client.delete_collection(collection_name)


def test_sha256_changes_when_content_changes(tmp_path: Path) -> None:
    p = tmp_path / "f.jsonl"
    p.write_text('{"a": 1}\n')
    a = compute_fixture_sha256(p)
    p.write_text('{"a": 2}\n')
    b = compute_fixture_sha256(p)
    assert a != b


def test_sha256_stable_across_calls(tmp_path: Path) -> None:
    p = tmp_path / "f.jsonl"
    p.write_text('{"a": 1}\n')
    assert compute_fixture_sha256(p) == compute_fixture_sha256(p)
```

- [x] **Step 3: Run the tests; expect failure**

Run: `uv run pytest tests/evals/test_corpus_fixture.py -v -m requires_qdrant`
Expected: import error / fail.

- [x] **Step 4: Implement `slopmortem/evals/corpus_fixture.py`**

```python
"""JSONL dump/restore + SHA for the eval corpus fixture.

The fixture is a regenerable artifact (run `just eval-record-corpus`); this
module's job is to bridge between a populated Qdrant collection and a
human-diffable JSONL file.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from qdrant_client import AsyncQdrantClient


_SCROLL_LIMIT = 256
_UPSERT_BATCH = 64


def compute_fixture_sha256(path: Path) -> str:
    """Return the sha256 hex of the file at `path`."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


async def dump_collection_to_jsonl(
    client: AsyncQdrantClient, collection: str, out_path: Path
) -> None:
    """Scroll every point in `collection` and write one JSON object per line.

    Each line contains `{canonical_id, dense, sparse_indices, sparse_values, payload}`.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    offset = None
    rows: list[dict[str, object]] = []
    while True:
        points, next_offset = await client.scroll(
            collection_name=collection,
            limit=_SCROLL_LIMIT,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        for p in points:
            payload = p.payload or {}
            vectors = p.vector or {}
            # Production stores dense under a named slot and sparse under another;
            # surface both. Names match what `qdrant_store` declares in
            # `ensure_collection`; verify when this lands.
            dense = vectors.get("dense") if isinstance(vectors, dict) else None
            sparse = vectors.get("sparse") if isinstance(vectors, dict) else None
            sparse_indices = list(sparse.indices) if sparse is not None else []
            sparse_values = list(sparse.values) if sparse is not None else []
            rows.append({
                "canonical_id": payload.get("canonical_id"),
                "dense": list(dense) if dense is not None else [],
                "sparse_indices": sparse_indices,
                "sparse_values": sparse_values,
                "payload": payload,
            })
        if next_offset is None:
            break
        offset = next_offset
    # Sort by canonical_id for stable diffs.
    rows.sort(key=lambda r: str(r.get("canonical_id") or ""))
    out_path.write_text("\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n")


async def restore_jsonl_to_collection(
    client: AsyncQdrantClient, collection: str, jsonl_path: Path
) -> None:
    """Read `jsonl_path` and bulk-upsert every line into `collection`.

    The collection must already exist with the correct vector configuration
    (use `slopmortem.corpus.qdrant_store.ensure_collection` to create it).
    """
    from qdrant_client.models import PointStruct, SparseVector  # noqa: PLC0415

    points: list[PointStruct] = []
    with jsonl_path.open() as f:
        for idx, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            sparse = SparseVector(
                indices=list(data.get("sparse_indices") or []),
                values=list(data.get("sparse_values") or []),
            )
            point = PointStruct(
                id=idx,
                vector={"dense": list(data["dense"]), "sparse": sparse},
                payload=data.get("payload") or {},
            )
            points.append(point)
            if len(points) >= _UPSERT_BATCH:
                await client.upsert(collection_name=collection, points=points)
                points = []
    if points:
        await client.upsert(collection_name=collection, points=points)
```

Note: `client.scroll`, `with_vectors=True`, and the `dense`/`sparse` named-slot return shape must match what `slopmortem/corpus/qdrant_store.ensure_collection` actually creates. Verify by reading `slopmortem/corpus/qdrant_store.py:50-74`. If the named slots differ (e.g. `text_dense` / `text_sparse`), update the field names here.

- [x] **Step 5: Run the tests, fix any Qdrant slot-name mismatches**

Run: `docker compose up -d qdrant && uv run pytest tests/evals/test_corpus_fixture.py -v -m requires_qdrant`
Expected: PASS.

- [x] **Step 6: Run the full suite + typecheck**

Run: `just test && just typecheck`
Expected: green (Qdrant tests skip when not available; under `docker compose up -d qdrant` they run).

---

## Task 3: Recording helper + ephemeral Qdrant context manager

Owner: one subagent.

**Files:**
- Modify: `slopmortem/pipeline.py:84-93` (add `sparse_encoder` parameter)
- Create: `slopmortem/evals/qdrant_setup.py`
- Create: `slopmortem/evals/recording_helper.py`
- Create: `tests/evals/test_recording_helper.py`

### 3A — Thread `sparse_encoder` through `pipeline.run_query` (B1 from spec review)

- [x] **Step 1: Write a failing test for sparse_encoder pass-through**

Add to `tests/test_pipeline_e2e.py` (a new test, not modifying the migrated one):

```python
async def test_run_query_forwards_sparse_encoder(tmp_path) -> None:
    """run_query forwards sparse_encoder to retrieve(); production fastembed not loaded."""
    seen_calls: list[str] = []

    def my_sparse(text: str) -> dict[int, float]:
        seen_calls.append(text)
        return {1: 1.0}

    # Build the same minimal fakes the existing test uses, but pass sparse_encoder.
    # ... (operator wires fakes; this test is a contract check)
    # Assert seen_calls is non-empty after run_query — i.e. retrieve()'s sparse path
    # actually invoked the injected encoder.
```

(Operator: write a more concrete version of this test; the contract is "if `sparse_encoder=` is passed to `run_query`, that callable replaces the fastembed lazy-load path inside `retrieve()`.")

- [x] **Step 2: Run the test; expect `TypeError: run_query() got an unexpected keyword argument 'sparse_encoder'`**

Run: `uv run pytest tests/test_pipeline_e2e.py::test_run_query_forwards_sparse_encoder -v`

- [x] **Step 3: Add `sparse_encoder` parameter to `run_query` in `slopmortem/pipeline.py:84-93`**

Modify the signature:

```python
async def run_query(
    input_ctx: InputContext,
    *,
    llm: LLMClient,
    embedding_client: EmbeddingClient,
    corpus: Corpus,
    config: Config,
    budget: Budget,
    progress: Callable[[str], None] | None = None,
    sparse_encoder: SparseEncoder | None = None,
) -> Report:
```

(Import `SparseEncoder` from `slopmortem.stages.retrieve` — it's already exported there as a `type` alias.)

Forward to `retrieve()`:

```python
retrieved = await retrieve(
    description=input_ctx.description,
    facets=facets,
    corpus=corpus,
    embedding_client=embedding_client,
    cutoff_iso=cutoff_iso,
    strict_deaths=config.strict_deaths,
    k_retrieve=config.K_retrieve,
    sparse_encoder=sparse_encoder,
)
```

Update the `run_query` docstring to mention `sparse_encoder`.

- [x] **Step 4: Run the test; expect PASS**

Run: `uv run pytest tests/test_pipeline_e2e.py::test_run_query_forwards_sparse_encoder -v`

### 3B — `setup_ephemeral_qdrant()` async context manager

- [x] **Step 5: Implement `slopmortem/evals/qdrant_setup.py`**

```python
"""Async context manager that spins a uniquely-named Qdrant collection,
populates it from a JSONL fixture, and drops it on exit."""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from qdrant_client import AsyncQdrantClient

from slopmortem.corpus.qdrant_store import QdrantCorpus, ensure_collection
from slopmortem.evals.corpus_fixture import restore_jsonl_to_collection

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@asynccontextmanager
async def setup_ephemeral_qdrant(
    fixture_path: Path,
    *,
    qdrant_url: str = "http://localhost:6333",
    collection_prefix: str = "slopmortem_eval_",
    post_mortems_root: Path | None = None,
    dim: int = 768,
) -> AsyncIterator[QdrantCorpus]:
    """Spin a uniquely-named collection, populate from JSONL, drop on exit.

    Collection name embeds `pid + uuid4` so a leak from `kill -9` is
    identifiable and droppable manually. No startup sweep — see Risk 4 of
    the spec for why a prefix-wide sweep is unsafe under pytest-xdist.
    """
    name = f"{collection_prefix}{os.getpid()}_{uuid.uuid4().hex}"
    client = AsyncQdrantClient(url=qdrant_url)
    try:
        await ensure_collection(client, name, dim=dim)
        await restore_jsonl_to_collection(client, name, fixture_path)
        corpus = QdrantCorpus(
            client=client,
            collection=name,
            post_mortems_root=post_mortems_root or Path("/tmp/slopmortem_eval"),
        )
        yield corpus
    finally:
        try:
            await client.delete_collection(name)
        except Exception:  # noqa: BLE001 — best-effort cleanup; orphans manual
            pass
        await client.close()
```

`dim=768` matches the fastembed default (`nomic-ai/nomic-embed-text-v1.5`). The recording helper passes the active embedder's dim explicitly.

### 3C — `record_cassettes_for_inputs()` orchestration helper (Layer 2)

- [x] **Step 6: Write failing test for the helper**

Create `tests/evals/test_recording_helper.py`:

```python
"""Tests for record_cassettes_for_inputs(): atomic swap, tmp cleanup, Tavily forced off."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

from slopmortem.evals.recording_helper import (
    _atomic_swap,
    _sweep_stale_recording_dirs,
    record_cassettes_for_inputs,
)

pytestmark = pytest.mark.requires_qdrant


def test_sweep_removes_only_stale_recording_dirs(tmp_path: Path) -> None:
    fresh = tmp_path / "scope.42.abc.recording"
    fresh.mkdir()
    stale = tmp_path / "scope.99.def.recording"
    stale.mkdir()
    # Touch back 25h.
    old_mtime = fresh.stat().st_mtime - 25 * 3600
    os.utime(stale, (old_mtime, old_mtime))
    # Sibling that's not a tmp dir; never touched.
    keep = tmp_path / "scope"
    keep.mkdir()

    _sweep_stale_recording_dirs(tmp_path, max_age_seconds=24 * 3600)

    assert fresh.exists()
    assert not stale.exists()
    assert keep.exists()


def test_atomic_swap_uses_two_step_rename(tmp_path: Path) -> None:
    real = tmp_path / "scope"
    real.mkdir()
    (real / "old.json").write_text("old")
    new_tmp = tmp_path / "scope.42.abc.recording"
    new_tmp.mkdir()
    (new_tmp / "new.json").write_text("new")

    _atomic_swap(tmp_dir=new_tmp, real_dir=real)

    assert (real / "new.json").exists()
    assert not (real / "old.json").exists()
    # No half-populated state; no leftover .old / .recording siblings.
    assert not new_tmp.exists()
    assert not (real.parent / (real.name + ".old")).exists()


def test_atomic_swap_handles_missing_real_dir(tmp_path: Path) -> None:
    real = tmp_path / "scope"
    new_tmp = tmp_path / "scope.42.abc.recording"
    new_tmp.mkdir()
    (new_tmp / "new.json").write_text("new")

    _atomic_swap(tmp_dir=new_tmp, real_dir=real)

    assert (real / "new.json").exists()
```

- [x] **Step 7: Run the failing tests**

Run: `uv run pytest tests/evals/test_recording_helper.py -v`
Expected: import error.

- [x] **Step 8: Implement `slopmortem/evals/recording_helper.py`**

```python
"""Layer-2 reusable helper: record cassettes for a set of inputs end-to-end.

Owns the ephemeral-Qdrant lifecycle, the recording wrappers, the
two-step atomic dir swap, and the Tavily-off override. Test authors call
this when they want a per-test cassette set without re-implementing the
plumbing.
"""

from __future__ import annotations

import os
import shutil
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from slopmortem.evals.qdrant_setup import setup_ephemeral_qdrant
from slopmortem.evals.recording import (
    RecordingEmbeddingClient,
    RecordingLLMClient,
    RecordingSparseEncoder,
)
from slopmortem.pipeline import run_query

if TYPE_CHECKING:
    from collections.abc import Iterator

    from slopmortem.config import Config
    from slopmortem.models import InputContext


_DEFAULT_MAX_COST_USD = 2.0
_STALE_TMP_SECONDS = 24 * 3600


def _sweep_stale_recording_dirs(root: Path, *, max_age_seconds: int) -> None:
    """Remove `*.recording` dirs older than `max_age_seconds` under `root`."""
    if not root.exists():
        return
    cutoff = time.time() - max_age_seconds
    for d in root.glob("**/*.recording"):
        try:
            if d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _atomic_swap(*, tmp_dir: Path, real_dir: Path) -> None:
    """Two-step rename: real → real.old, tmp → real, rmtree real.old.

    POSIX `rename(2)` requires an empty destination, so we pre-rename the
    existing real_dir out of the way before moving the tmp dir in. A
    SIGKILL between the two replaces leaves either real_dir intact under
    `.old` or the new dir under real_dir; never a half-populated tmp_dir
    under the canonical name.
    """
    old = real_dir.parent / (real_dir.name + ".old")
    # Idempotent cleanup of any leftover `.old` from a prior crash.
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)
    if real_dir.exists():
        os.replace(real_dir, old)
    real_dir.parent.mkdir(parents=True, exist_ok=True)
    os.replace(tmp_dir, real_dir)
    shutil.rmtree(old, ignore_errors=True)


@contextmanager
def _tavily_off(config: Config) -> Iterator[Config]:
    """Yield a `Config` copy with `enable_tavily_synthesis=False` (Risk 6)."""
    yield config.model_copy(update={"enable_tavily_synthesis": False})


async def record_cassettes_for_inputs(
    *,
    inputs: list[InputContext],
    output_dir: Path,
    corpus_fixture_path: Path,
    config: Config,
    qdrant_url: str = "http://localhost:6333",
    max_cost_usd: float = _DEFAULT_MAX_COST_USD,
) -> None:
    """Record cassettes for every input in `inputs` under `output_dir/<name>/`.

    Args:
        inputs: One `InputContext` per scope to record.
        output_dir: Parent directory; one subdir per `input.name`.
        corpus_fixture_path: JSONL fixture used to populate ephemeral Qdrant.
        config: Live config (the helper forces `enable_tavily_synthesis=False`).
        qdrant_url: Qdrant URL for the ephemeral collection.
        max_cost_usd: Cost ceiling for the LLM recording wrapper.
    """
    # Lazy imports so import-time cycles stay cheap.
    from slopmortem.cli import _build_deps  # pyright: ignore[reportPrivateUsage]
    from slopmortem.corpus.tools_impl import _set_corpus  # pyright: ignore[reportPrivateUsage]

    output_dir.mkdir(parents=True, exist_ok=True)
    _sweep_stale_recording_dirs(output_dir, max_age_seconds=_STALE_TMP_SECONDS)

    with _tavily_off(config) as cfg:
        async with setup_ephemeral_qdrant(
            corpus_fixture_path,
            qdrant_url=qdrant_url,
        ) as corpus:
            _set_corpus(corpus)
            llm, embedder, _live_corpus, budget = _build_deps(cfg)
            # We use the helper's ephemeral corpus; ignore _live_corpus.
            del _live_corpus

            # One recording wrapper covers every stage. Stage filename prefix
            # comes from the registry; filename model slug comes from the
            # per-call `model=` arg.
            from slopmortem.llm.prompts import prompt_template_sha  # noqa: PLC0415
            from slopmortem.llm.tools import synthesis_tools  # noqa: PLC0415
            from slopmortem.models import Facets, LlmRerankResult, Synthesis  # noqa: PLC0415
            stage_registry = {
                prompt_template_sha("facet_extract", tools=None, response_format=Facets): "facet_extract",
                prompt_template_sha("llm_rerank", tools=None, response_format=LlmRerankResult): "llm_rerank",
                prompt_template_sha("synthesize", tools=synthesis_tools(cfg), response_format=Synthesis): "synthesize",
            }

            for ctx in inputs:
                # Share the directory-naming function with the replay path
                # (`slopmortem.evals.runner._row_id`) so anonymous inputs
                # (`ctx.name == ""`) write to the same `<sha1[:8]>` dir that
                # replay reads from. P3.
                from slopmortem.evals.runner import _row_id  # noqa: PLC0415; pyright: ignore[reportPrivateUsage]
                scope_name = _row_id(ctx)
                real_dir = output_dir / scope_name
                tmp_dir = output_dir / f"{scope_name}.{os.getpid()}.{uuid.uuid4().hex}.recording"
                tmp_dir.mkdir(parents=True, exist_ok=False)
                try:
                    rec_llm = RecordingLLMClient(
                        inner=llm,
                        out_dir=tmp_dir,
                        max_cost_usd=max_cost_usd,
                        stage_registry=stage_registry,
                    )
                    rec_embed = RecordingEmbeddingClient(inner=embedder, out_dir=tmp_dir)

                    from slopmortem.corpus.embed_sparse import encode as live_sparse  # noqa: PLC0415
                    rec_sparse = RecordingSparseEncoder(inner=live_sparse, out_dir=tmp_dir)

                    _ = await run_query(
                        ctx,
                        llm=rec_llm,
                        embedding_client=rec_embed,
                        corpus=corpus,
                        config=cfg,
                        budget=budget,
                        sparse_encoder=rec_sparse,
                    )
                except Exception:
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                    raise
                else:
                    _atomic_swap(tmp_dir=tmp_dir, real_dir=real_dir)
```

Note on the single-wrapper design: `RecordingLLMClient` derives both the
filename stage prefix and the model slug per call (from
`extra_body['prompt_template_sha']` lookup in the registry, and from the
per-call `model=` arg). One wrapper records facet, rerank, and synthesize
under the same cost ceiling — and the same shape works for `corpus_recorder`,
which fans out into `summarize`, `facet_extract`, and the entity-resolution
tiebreaker through the same `LLMClient`. No model-routing dispatcher needed.

- [x] **Step 9: Run the recording-helper tests**

Run: `uv run pytest tests/evals/test_recording_helper.py -v`
Expected: PASS for the unit tests (`test_sweep_*`, `test_atomic_swap_*`). The full Qdrant-backed recording test runs under `requires_qdrant + RUN_LIVE`; defer that to commit 5 (operator).

- [x] **Step 10: Run full suite + typecheck**

Run: `just test && just typecheck`
Expected: green.

---

## Task 4: Justfile + runner argparse (no behavior change)

Owner: one subagent.

**Files:**
- Create: `.gitattributes` (single LFS line)
- Modify: `flake.nix:100-121` (add `git-lfs`)
- Modify: `justfile:24-25` (rewire `eval-record`; add `eval-record-corpus`)
- Modify: `slopmortem/evals/runner.py` (`--scope`, `--max-cost-usd`, real `--record` wiring)
- Modify: `tests/test_eval_runner.py:209-227` (rewrite `test_runner_record_flag_is_deferred`)

The runner default is unchanged here — replay still uses canned. We only land argparse + justfile + LFS so commit 5 (operator) has the entry points.

- [x] **Step 1: Add `.gitattributes` for LFS on the corpus fixture**

Create `/Users/vaporif/Repos/premortem/.gitattributes`:

```
tests/fixtures/corpus_fixture.jsonl filter=lfs diff=lfs merge=lfs -text
```

- [x] **Step 2: Add `git-lfs` to the nix dev shell**

Modify `flake.nix:100-121`. Add `git-lfs` to the `packages` list. Verify with: `nix develop -c which git-lfs` (operator runs this; subagent only edits the file).

- [x] **Step 3: Rewire `eval-record` and add `eval-record-corpus` in `justfile`**

Modify `justfile:23-25` to:

```just
# Re-record cassettes against live OpenRouter + local fastembed; LLM-side cost only.
# Run sparingly. Default cost ceiling --max-cost-usd=2.0 in the runner.
eval-record:
    RUN_LIVE=1 uv run python -m slopmortem.evals.runner \
        --dataset tests/evals/datasets/seed.jsonl \
        --baseline tests/evals/baseline.json \
        --record \
        --max-cost-usd 2.0

# Regenerate the seed corpus fixture from corpus_fixture_inputs.yml. Run rarely.
# Cost: ~$0.30-$1 under the default fastembed embedding provider.
eval-record-corpus:
    RUN_LIVE=1 uv run python -m slopmortem.evals.corpus_recorder \
        --inputs tests/fixtures/corpus_fixture_inputs.yml \
        --out tests/fixtures/corpus_fixture.jsonl
```

`slopmortem.evals.corpus_recorder` is a small new module owned by Task 5 (operator) — it's a CLI wrapper around `slopmortem.ingest.ingest` + `dump_collection_to_jsonl`. To unblock the just target now, also add a stub in this commit:

Create `slopmortem/evals/corpus_recorder.py` (stub for Task 4; full impl during Task 5):

```python
"""CLI: regenerate `corpus_fixture.jsonl` by running real ingest then dumping."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="slopmortem.evals.corpus_recorder")
    p.add_argument("--inputs", required=True)
    p.add_argument("--out", required=True)
    _ = p.parse_args(argv)
    print("eval-record-corpus is operator-only; full implementation lands in commit 5", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
```

Note: `eval-record-corpus` is operator-only and cannot run in CI. The stub returning exit 1 is deliberate so a CI-side accidental invocation fails loudly; commit 5 operator replaces it.

Update the `# Default eval runs against cassettes...` comment on `justfile:19` — **leave the comment alone for now.** Editing it in commit 4 creates a false-precondition window between commits 4 and 6. Defer the comment fix to Task 6.

- [x] **Step 4: Add `--scope` and `--max-cost-usd` to the runner argparse, wire `--record` to the helper**

Modify `slopmortem/evals/runner.py`:

In `_build_argparser()` add:

```python
_ = p.add_argument(
    "--scope",
    type=str,
    default=None,
    help=(
        "Filter to one row by name (record or replay). Without --scope, "
        "every row in the dataset runs."
    ),
)
_ = p.add_argument(
    "--max-cost-usd",
    type=float,
    default=2.0,
    help=(
        "Cost ceiling per recording session ($). Only consulted in --record mode. "
        "Override if a re-record legitimately needs more."
    ),
)
```

In `main()` replace the `if record:` deferred-stub branch with a real call into the recording helper. New flow:

```python
if record:
    from slopmortem.evals.recording_helper import record_cassettes_for_inputs  # noqa: PLC0415
    from slopmortem.config import load_config  # noqa: PLC0415

    rows = _load_dataset(dataset_path)
    if scope is not None:
        rows = [r for r in rows if r.name == scope]
        if not rows:
            print(f"unknown scope {scope!r}; valid: {[r.name for r in _load_dataset(dataset_path)]}", file=sys.stderr)
            sys.exit(2)
    cfg = load_config()
    output_dir = Path("tests/fixtures/cassettes/evals")
    corpus_fixture_path = Path("tests/fixtures/corpus_fixture.jsonl")
    if not corpus_fixture_path.exists():
        print(
            f"missing {corpus_fixture_path}; run `just eval-record-corpus` first",
            file=sys.stderr,
        )
        sys.exit(2)
    asyncio.run(
        record_cassettes_for_inputs(
            inputs=rows, output_dir=output_dir, corpus_fixture_path=corpus_fixture_path,
            config=cfg, max_cost_usd=max_cost_usd,
        )
    )
    sys.exit(0)
```

`asyncio.run` accepts a coroutine, so kwargs flow through naturally — no wrapper closure needed.

Note: this commit only **wires** `--record` to the helper. Replay path (when `record` is False) still uses the existing canned-mode code. The runner default flips to cassettes in Task 6.

- [x] **Step 5: Rewrite `tests/test_eval_runner.py:209-227`**

Replace `test_runner_record_flag_is_deferred` with two tests: a unit test that confirms argparse wires `--record`, and a (skipped-by-default) live test that exercises the helper end-to-end. The live test goes behind `@pytest.mark.skipif(not os.environ.get("RUN_LIVE"))`.

```python
def test_runner_record_flag_invokes_helper(monkeypatch, tmp_path):
    """--record dispatches to record_cassettes_for_inputs via asyncio.run."""
    seed = _write_seed(tmp_path, [{"name": "alpha", "description": "x"}])
    baseline = tmp_path / "baseline.json"
    baseline.write_text("{}")

    seen: dict[str, object] = {}

    async def fake_helper(*, inputs, output_dir, corpus_fixture_path, config, max_cost_usd):
        seen["called"] = True
        seen["inputs"] = inputs
        seen["max_cost_usd"] = max_cost_usd

    monkeypatch.setattr(
        "slopmortem.evals.recording_helper.record_cassettes_for_inputs",
        fake_helper,
    )
    # Stub the corpus fixture so the existence check passes.
    cf = tmp_path / "corpus_fixture.jsonl"
    cf.write_text("")
    monkeypatch.chdir(tmp_path)
    Path("tests/fixtures").mkdir(parents=True, exist_ok=True)
    cf2 = Path("tests/fixtures/corpus_fixture.jsonl")
    cf2.write_text("")

    with pytest.raises(SystemExit) as exc_info:
        runner.main([
            "--dataset", str(seed),
            "--baseline", str(baseline),
            "--record",
            "--max-cost-usd", "1.5",
        ])
    assert exc_info.value.code == 0
    assert seen["called"] is True
    assert seen["max_cost_usd"] == pytest.approx(1.5)
```

Delete the old `test_runner_record_flag_is_deferred` body and the `_RECORD_DEFERRED_MSG` constant in `runner.py`.

- [x] **Step 6: Run the test**

Run: `uv run pytest tests/test_eval_runner.py -v`
Expected: PASS.

- [ ] **Step 7: Run full suite + typecheck + lint**

Run: `just test && just typecheck && just lint`
Expected: green.

---

## Task 5: Operator — generate fixtures (manual)

**Not assignable to a subagent.** The user runs this against live APIs and commits the artifacts.

**Preconditions (one-time per machine):**
- `git lfs install` (idempotent; the flake.nix change in Task 4 makes `git-lfs` available in the dev shell).
- `.gitattributes` (Task 4) is in place so the JSONL is captured as LFS on first add.
- `slopmortem embed-prefetch` (downloads nomic-dense + Qdrant/bm25 fastembed models, ~700 MB combined; idempotent).

**Operator runs:**
```bash
git lfs install                           # one-time
docker compose up -d qdrant
slopmortem embed-prefetch                 # one-time fastembed cache warm
RUN_LIVE=1 just eval-record-corpus        # ~$0.30-$1 under fastembed default
RUN_LIVE=1 just eval-record               # ~$0.50-$1, ceiling --max-cost-usd=2.0 (LLM-side only)
```

**Operator deliverables (committed):**
- `tests/fixtures/corpus_fixture_inputs.yml` (grown to ~30 entries from the Task 2 scaffold)
- `tests/fixtures/corpus_fixture_bodies/<canonical_id>.md` (~30 hand-curated body files; regular git diffs)
- `tests/fixtures/corpus_fixture.jsonl` (~1.5 MB, via LFS — `git diff` shows LFS pointer, not floats)
- `tests/fixtures/cassettes/evals/<row_id>/*.json` (10 dirs, ~70 files; regular git diffs)
- Updated `tests/evals/baseline.json` (v2)

**Note:** the full `slopmortem/evals/corpus_recorder.py` implementation (replacing the stub from Task 4) lands in this commit too. Cost-ceiling enforcement uses `Budget(cap_usd=...)` (which already raises `BudgetExceededError` from `slopmortem/budget.py:11-43`) — there is no need to wrap `llm` in `RecordingLLMClient` for `eval-record-corpus`, because corpus regen produces a JSONL artifact, not cassettes (replay loads the JSONL directly into ephemeral Qdrant; see spec §"Data flow at replay time"). Per-row `eval-record` still wraps with `RecordingLLMClient` because that flow records cassettes.

**Seed-YAML format (`tests/fixtures/corpus_fixture_inputs.yml`).** Body text
lives in sibling `tests/fixtures/corpus_fixture_bodies/<id>.md` files
(referenced by `body_path:`). Inline body in YAML is operator-hostile —
escaping landmines for code blocks, quotes, etc. Sibling text files diff
cleanly. Schema:

```yaml
- canonical_id: stripe_2024_outage_a
  source: seed
  source_id: stripe_2024_outage_a
  body_path: corpus_fixture_bodies/stripe_2024_outage_a.md
  url: null
  fetched_at: "2026-04-29T00:00:00Z"
- canonical_id: webvan_postmortem_a
  source: seed
  source_id: webvan_postmortem_a
  body_path: corpus_fixture_bodies/webvan_postmortem_a.md
  url: null
  fetched_at: "2026-04-29T00:00:00Z"
# ~30 entries total
```

```python
"""CLI: regenerate `corpus_fixture.jsonl` by running real ingest then dumping."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import asyncio
import yaml
from qdrant_client import AsyncQdrantClient

from slopmortem.budget import Budget
from slopmortem.config import load_config
from slopmortem.corpus.qdrant_store import QdrantCorpus, ensure_collection
from slopmortem.evals.corpus_fixture import dump_collection_to_jsonl
from slopmortem.ingest import MergeJournal, ingest
from slopmortem.models import RawEntry, SlopJudgement


# Ingest's LLM-side cost is dominated by summarize + facet_extract per doc.
# ~30 docs × ~3 calls × ~$0.01-$0.05 ≈ $1-5 on a clean run; ceiling at $5
# stops a single model-upgrade surprise from blowing through silently.
# Operator overrides via --max-cost-usd if a re-record legitimately needs more.
_DEFAULT_CORPUS_MAX_COST_USD = 5.00


class _SeedInputSource:
    """Adapter: read seed YAML + body files, yield RawEntry per doc.

    Bespoke for cassette regen: deterministic (no live HTTP, no extract_clean
    drift), so the same seed YAML + body files always produce the same RawEntry
    stream. CuratedSource is wrong here because it re-fetches URLs, and live
    page content drifts between regens.
    """

    def __init__(self, yaml_path: Path) -> None:
        self._yaml_path = yaml_path

    async def fetch(self) -> AsyncIterator[RawEntry]:
        rows = yaml.safe_load(self._yaml_path.read_text()) or []
        base = self._yaml_path.parent
        for row in rows:
            body_path = base / row["body_path"]
            yield RawEntry(
                source=row["source"],
                source_id=row["source_id"],
                url=row.get("url"),
                fetched_at=row["fetched_at"],
                body=body_path.read_text(),
                # Seed inputs are hand-curated; canonical_id is provided
                # directly rather than derived. The ingest pipeline normally
                # canonicalizes — operator confirms canonical_id matches what
                # canonicalize would have produced (or extends ingest to
                # short-circuit canonicalization for `source == "seed"`).
            )


class _PermissiveSlopClassifier:
    """Returns 'not slop' for every input.

    Seed corpus is hand-vetted, so real Binoculars would also pass everything;
    the stub avoids loading a ~few-hundred-MB model at corpus regen time and
    avoids version-drift surprises where a Binoculars update unexpectedly
    filters a hand-curated doc.
    """

    async def classify(self, text: str) -> SlopJudgement:  # pyright: ignore[reportUnusedParameter]
        return SlopJudgement(is_slop=False, score=0.0)


async def _record(inputs_path: Path, out_path: Path, *, max_cost_usd: float) -> None:
    cfg = load_config()
    name = f"slopmortem_corpus_record_{os.getpid()}_{uuid.uuid4().hex}"
    qdrant_url = cfg.qdrant_url
    client = AsyncQdrantClient(url=qdrant_url)
    with tempfile.TemporaryDirectory(prefix="corpus_record_") as scratch_str:
        scratch = Path(scratch_str)
        try:
            from slopmortem.llm.embedding_client import EMBED_DIMS  # noqa: PLC0415
            await ensure_collection(client, name, dim=EMBED_DIMS[cfg.embed_model_id])

            # Build live deps. No RecordingLLMClient wrapping: Budget handles
            # the cost cap (BudgetExceededError raises inside reserve()) and
            # corpus regen does not produce cassettes — the JSONL is the
            # artifact replay consumes.
            from slopmortem.cli import _build_deps  # noqa: PLC0415; pyright: ignore[reportPrivateUsage]
            llm, embed_client, _live_corpus, _ignored_budget = _build_deps(cfg)
            del _live_corpus, _ignored_budget

            from slopmortem.corpus.embed_sparse import encode as live_sparse  # noqa: PLC0415

            corpus = QdrantCorpus(client=client, collection=name, embed_model_id=cfg.embed_model_id)
            journal = await MergeJournal.open(scratch / "journal.sqlite")
            try:
                await ingest(
                    sources=[_SeedInputSource(inputs_path)],
                    enrichers=[],
                    journal=journal,
                    corpus=corpus,
                    llm=llm,
                    embed_client=embed_client,
                    budget=Budget(cap_usd=max_cost_usd),
                    slop_classifier=_PermissiveSlopClassifier(),
                    config=cfg,
                    post_mortems_root=scratch / "post_mortems",
                    sparse_encoder=live_sparse,
                )
            finally:
                await journal.close()

            out_tmp = out_path.with_suffix(out_path.suffix + ".recording")
            await dump_collection_to_jsonl(client, name, out_tmp)
            os.replace(out_tmp, out_path)
            print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")
        finally:
            try:
                await client.delete_collection(name)
            except Exception:  # noqa: BLE001 — best-effort cleanup
                pass
            await client.close()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="slopmortem.evals.corpus_recorder")
    p.add_argument("--inputs", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument(
        "--max-cost-usd", type=float, default=_DEFAULT_CORPUS_MAX_COST_USD,
        help="Cost ceiling enforced via Budget(cap_usd=...).",
    )
    ns = p.parse_args(argv)
    if not os.environ.get("RUN_LIVE"):
        print("eval-record-corpus requires RUN_LIVE=1 (live API spend)", file=sys.stderr)
        sys.exit(2)
    asyncio.run(_record(ns.inputs, ns.out, max_cost_usd=ns.max_cost_usd))


if __name__ == "__main__":
    main()
```

`ingest` signature lookup: `slopmortem/ingest.py:653` — `async def ingest(*, sources, enrichers, journal, corpus, llm, embed_client, budget, slop_classifier, config, post_mortems_root, dry_run, force, sparse_encoder)`. The call above maps every required keyword. Operator verifies `RawEntry` field names against `slopmortem/models.py:202` at implementation time — if the model defines `body` under a different field name (e.g. `text`), update `_SeedInputSource.fetch()` accordingly.

**Validation (P9 pass criteria — all must hold):**

1. **Distinct canonical_id count matches seed input count:**
   ```bash
   python -c "
   import json
   from pathlib import Path
   import yaml
   inputs = yaml.safe_load(Path('tests/fixtures/corpus_fixture_inputs.yml').read_text())
   ids_jsonl = {json.loads(l)['canonical_id'] for l in Path('tests/fixtures/corpus_fixture.jsonl').read_text().splitlines() if l.strip()}
   assert len(ids_jsonl) == len(inputs), f'distinct canonical_ids in JSONL ({len(ids_jsonl)}) != seed inputs ({len(inputs)}) — silent dedup or missing doc'
   "
   ```
   (Strict equality: a duplicate `canonical_id` in the seed YAML or silent ingest drop is loud.)

2. **JSONL schema validates per row** — every line parses as JSON and contains required fields `canonical_id`, `dense`, `sparse_indices`, `sparse_values`, `payload`. Run via:
   ```bash
   python -c "
   import json
   from pathlib import Path
   for i, line in enumerate(Path('tests/fixtures/corpus_fixture.jsonl').read_text().splitlines(), 1):
       row = json.loads(line)
       missing = {'canonical_id', 'dense', 'sparse_indices', 'sparse_values', 'payload'} - row.keys()
       assert not missing, f'line {i}: missing {missing}'
   "
   ```

3. **Round-trip restores cleanly** — `restore_jsonl_to_collection` against the freshly-recorded file completes without raising, and the resulting collection's point count matches `wc -l`.

4. **`just eval` against the freshly-recorded set returns exit 0** (after operator runs `just eval-record` to fill in the per-row cassettes; this is the integration-level gate).

5. `git lfs ls-files` shows `tests/fixtures/corpus_fixture.jsonl` is tracked by LFS.

---

## Task 6: Switch runner default to cassettes; remove canned helpers

Owner: one subagent. Depends on Task 5 (operator must have committed fixtures + cassettes first).

**Files:**
- Modify: `slopmortem/corpus/store.py` (extend `Corpus` Protocol with `lookup_sources` — P18 fix)
- Modify: `slopmortem/corpus/qdrant_store.py` (implement `lookup_sources` on `QdrantCorpus`)
- Modify: `slopmortem/evals/runner.py` (replace `_build_canned`/`_EvalCorpus`/`_run_deterministic`; rewrite `_allowed_hosts_for_candidate` to consume a pre-fetched sources map; add v2 baseline; per-row continuation)
- Modify: `justfile:19` (correct the `# Default eval...` comment)
- Create: `tests/evals/test_runner_replay.py`
- Create: `tests/evals/test_fixtures/tiny_corpus.jsonl`
- Create: `tests/evals/test_fixtures/cassettes/evals/<row_id>/*.json` (hand-built, ~3 LLM + ~3 dense + ~3 sparse)

### 6Pre — Extend `Corpus` Protocol with `lookup_sources` (P18 fix)

The previous deterministic-mode runner unioned a fixed host allowlist with each candidate's own `payload.sources`. The cassette runner needs the same data, but the `Report` object only carries `Synthesis` (no payloads). Rather than collapse to the fixed allowlist (P18 regression), extend the read-side Protocol so any `Corpus` implementation — `QdrantCorpus` in cassette and live mode, fakes in tests — can answer "what are the sources for this candidate id?" The same accessor lifts the live-mode strictness reduction too.

- [ ] **Step 0a: Add `lookup_sources` to `Corpus` Protocol**

Modify `slopmortem/corpus/store.py`:

```python
async def lookup_sources(self, canonical_id: str) -> list[str]:
    """Return the persisted source URLs for *canonical_id*, or [] if unknown.

    Used by eval scoring to compute per-candidate `allowed_hosts` (union
    of fixed allowlist + the candidate's own sources). Implementations
    that cannot look up payload should return [].
    """
    ...
```

- [ ] **Step 0b: Implement `lookup_sources` on `QdrantCorpus`**

Modify `slopmortem/corpus/qdrant_store.py`. The Qdrant payload already carries `sources`; expose it. If the canonical id is not present, return `[]` (caller treats absent → use fixed allowlist only).

```python
async def lookup_sources(self, canonical_id: str) -> list[str]:
    points = await self._client.retrieve(
        collection_name=self._collection,
        ids=[canonical_id],
        with_payload=["sources"],
        with_vectors=False,
    )
    if not points:
        return []
    raw = points[0].payload or {}
    sources = raw.get("sources", [])
    if not isinstance(sources, list):
        return []
    return [str(s) for s in sources]
```

- [ ] **Step 0c: Add a Protocol-conformance test**

Add to `tests/evals/test_runner_replay.py`:

```python
def test_qdrant_corpus_lookup_sources_returns_payload_urls() -> None:
    """Live + cassette modes both rely on this; protocol must expose it."""
```

### 6A — Bump baseline schema to v2; round-trip metadata

- [ ] **Step 1: Write failing tests for v2 round-trip + v1→v2 upgrade**

Add to `tests/evals/test_runner_replay.py`:

```python
"""Runner-replay integration tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.requires_qdrant


def test_baseline_v1_upgrades_to_v2_on_write(tmp_path: Path, monkeypatch) -> None:
    """A v1 baseline becomes v2 on `--write-baseline`; rows preserved."""
    # Operator wires the test against tiny_corpus.jsonl + hand-built cassettes.


def test_baseline_v2_round_trip_preserves_metadata(tmp_path: Path) -> None:
    """v2 baseline `corpus_fixture_sha256` and `recording_metadata` survive write+read."""


def test_corpus_sha_mismatch_emits_warning_not_failure(tmp_path: Path) -> None:
    """A SHA mismatch is a WARN line, exit 0."""


def test_runner_replay_passes_with_recorded_cassettes(tmp_path: Path) -> None:
    """End-to-end: ephemeral Qdrant + cassette dir → exit 0."""


def test_runner_replay_fails_loud_on_missing_cassette_dir(tmp_path: Path) -> None:
    """Missing cassette dir → FAIL <row_id>: no cassettes; exit 1; other rows continue."""


def test_runner_replay_fails_loud_on_llm_cassette_miss(tmp_path: Path) -> None:
    """Cassette key mismatch → FAIL <row_id>: cassette miss; exit 1."""


def test_runner_replay_fails_loud_on_embed_cassette_miss(tmp_path: Path) -> None:
    """`NoCannedEmbeddingError` per-row → FAIL <row_id>: cassette miss; exit 1."""


def test_runner_replay_scope_filter_applies_to_loop(tmp_path: Path) -> None:
    """`--scope <name>` runs only that row; baseline merge preserves untouched."""


def test_runner_replay_unknown_scope_is_fatal(tmp_path: Path) -> None:
    """`--scope notarow` exits 2 with the list of valid scopes."""


def test_switching_embed_model_id_produces_loud_cassette_miss() -> None:
    """Changing `Config.embed_model_id` between record and replay → NoCannedEmbeddingError."""
```

(Operator: each test gets a concrete body; the test list is the contract.)

- [ ] **Step 2: Run the failing tests**

Run: `uv run pytest tests/evals/test_runner_replay.py -v -m requires_qdrant`
Expected: most fail (skeletons).

- [ ] **Step 3: Update `_BASELINE_VERSION`, `_serialize_results`, `_diff_against_baseline`, `--write-baseline` in `slopmortem/evals/runner.py`**

Bump `_BASELINE_VERSION = 2`.

`_serialize_results` signature:

```python
def _serialize_results(
    results: dict[str, dict[str, object]],
    *,
    corpus_fixture_sha256: str,
    recording_metadata: dict[str, object],
) -> dict[str, object]:
    return {
        "version": _BASELINE_VERSION,
        "corpus_fixture_sha256": corpus_fixture_sha256,
        "recording_metadata": recording_metadata,
        "rows": results,
    }
```

`_diff_against_baseline` accepts a v1 or v2 dict. v1 → skip SHA check; v2 → emit WARN on mismatch:

```python
def _diff_against_baseline(
    current: dict[str, dict[str, object]],
    baseline: dict[str, object],
    *,
    current_corpus_sha: str | None = None,
) -> tuple[list[str], list[str]]:
    regressions: list[str] = []
    warnings: list[str] = []

    version = baseline.get("version", 1)
    if version == 2 and current_corpus_sha is not None:
        baseline_sha = baseline.get("corpus_fixture_sha256")
        if baseline_sha and baseline_sha != current_corpus_sha:
            warnings.append(
                f"corpus_fixture_sha256 mismatch: baseline={baseline_sha}, current={current_corpus_sha}"
            )

    raw_rows: object = baseline.get("rows", {}) if baseline else {}
    # ... rest unchanged
```

`--write-baseline` (and per-row `--scope`): when the existing baseline is v2, **merge** new row entries into the existing `rows` dict and preserve `corpus_fixture_sha256` + `recording_metadata`. When v1 (or empty), produce a fresh v2 envelope. Implementation outline:

```python
if write_baseline:
    existing = _load_baseline(baseline_path)
    new_rows: dict[str, dict[str, object]] = {}
    if existing.get("version") == 2:
        existing_rows_obj = existing.get("rows", {})
        if isinstance(existing_rows_obj, dict):
            new_rows.update(existing_rows_obj)  # pyright: ignore[reportUnknownArgumentType]
        new_rows.update(results)
        merged = _serialize_results(
            new_rows,
            corpus_fixture_sha256=str(existing.get("corpus_fixture_sha256", "") or current_corpus_sha or ""),
            recording_metadata=existing.get("recording_metadata", {}) or {},
        )
    else:
        merged = _serialize_results(
            results,
            corpus_fixture_sha256=current_corpus_sha or "",
            recording_metadata=_recording_metadata_from_config(cfg),
        )
    baseline_path.write_text(json.dumps(merged, indent=2, sort_keys=True) + "\n")
    sys.exit(0)
```

`_recording_metadata_from_config(cfg)`:

```python
def _recording_metadata_from_config(cfg: Config) -> dict[str, object]:
    return {
        "recorded_at": datetime.now(UTC).isoformat(),
        "models": {
            "facet": cfg.model_facet,
            "rerank": cfg.model_rerank,
            "synthesize": cfg.model_synthesize,
            "embedding": cfg.embed_model_id,
            "embedding_provider": cfg.embedding_provider,
        },
    }
```

### 6B — Replace the deterministic-mode runner body with cassette-backed replay

- [ ] **Step 4: Replace `_run_deterministic` with `_run_cassettes` + ephemeral Qdrant**

New flow:

```python
async def _run_cassettes(
    rows: list[InputContext], row_ids: list[str], scope_filter: str | None,
) -> dict[str, dict[str, object]]:
    cfg = load_config()
    fixture_path = Path("tests/fixtures/corpus_fixture.jsonl")
    if not fixture_path.exists():
        print(f"missing {fixture_path}; run `just eval-record-corpus` first", file=sys.stderr)
        sys.exit(2)

    from slopmortem.evals.cassettes import (  # noqa: PLC0415
        NoCannedEmbeddingError, load_embedding_cassettes, load_llm_cassettes,
    )
    from slopmortem.evals.qdrant_setup import setup_ephemeral_qdrant  # noqa: PLC0415
    from slopmortem.llm.cassettes import embed_cassette_key  # noqa: PLC0415
    from slopmortem.llm.fake import FakeLLMClient, NoCannedResponseError  # noqa: PLC0415
    from slopmortem.llm.fake_embeddings import FakeEmbeddingClient  # noqa: PLC0415

    from slopmortem.llm.openai_embeddings import EMBED_DIMS  # noqa: PLC0415
    dim = EMBED_DIMS[cfg.embed_model_id]

    async with setup_ephemeral_qdrant(fixture_path, dim=dim) as corpus:
        results: dict[str, dict[str, object]] = {}
        for ctx, rid in zip(rows, row_ids, strict=True):
            if scope_filter is not None and rid != scope_filter:
                continue
            scope_dir = Path("tests/fixtures/cassettes/evals") / rid
            if not scope_dir.exists() or not any(scope_dir.iterdir()):
                print(f"FAIL {rid}: no cassettes")
                results[rid] = {"candidates_count": 0, "assertions": {}}
                continue
            llm_canned = {
                k: FakeResponse(
                    text=v.text, stop_reason=v.stop_reason, cost_usd=v.cost_usd,
                    cache_read_tokens=v.cache_read_tokens, cache_creation_tokens=v.cache_creation_tokens,
                )
                for k, v in load_llm_cassettes(scope_dir).items()
            }
            dense_canned, sparse_canned = load_embedding_cassettes(scope_dir)
            fake_llm = FakeLLMClient(canned=llm_canned, default_model=cfg.model_synthesize)
            fake_embed = FakeEmbeddingClient(model=cfg.embed_model_id, canned=dense_canned)

            def cassette_sparse(text: str, _canned=sparse_canned) -> dict[int, float]:
                key = embed_cassette_key(text=text, model="Qdrant/bm25")
                if key not in _canned:
                    raise NoCannedEmbeddingError(f"no sparse cassette for {key!r}")
                idx, vals = _canned[key]
                return dict(zip(idx, vals, strict=True))

            try:
                report = await run_query(
                    ctx, llm=fake_llm, embedding_client=fake_embed, corpus=corpus,
                    config=cfg, budget=Budget(cap_usd=2.0), sparse_encoder=cassette_sparse,
                )
            except (NoCannedResponseError, NoCannedEmbeddingError) as exc:
                print(f"FAIL {rid}: cassette miss — {exc}")
                results[rid] = {"candidates_count": 0, "assertions": {}}
                continue
            # P18: pre-fetch each synthesized candidate's payload.sources via the
            # Corpus protocol so allowed_hosts unions the fixed allowlist with
            # the candidate's own URLs (parity with the deleted deterministic mode).
            sources_map = {
                s.candidate_id: await corpus.lookup_sources(s.candidate_id)
                for s in report.candidates
            }
            results[rid] = _score_report(report, sources_map=sources_map)
    return results
```

The `sources_map` is built from `corpus.lookup_sources` — the Protocol method added in 6Pre. Same shape works for `--live` mode (Step 6Live below), so `all_sources_in_allowed_domains` retains its full strictness in both modes.

In `main()`:
- Replace the call to `_run_deterministic` with `_run_cassettes`, passing `scope`.
- After `_run_cassettes`, compute `current_corpus_sha = compute_fixture_sha256(Path("tests/fixtures/corpus_fixture.jsonl"))`.
- Pass it to `_diff_against_baseline(..., current_corpus_sha=current_corpus_sha)` and to `_serialize_results` on `--write-baseline`.

- [ ] **Step 5: Remove the dead helpers; rewrite `_allowed_hosts_for_candidate`**

Delete from `slopmortem/evals/runner.py`:
- `_facets`, `_payload`, `_candidate`, `_facet_extract_payload`, `_rerank_payload`, `_synthesis_payload`
- `_build_canned`
- `_EvalCorpus`
- `_no_op_sparse_encoder`
- `_DETERMINISTIC_*_MODEL` constants
- `_build_deterministic_config`
- `_run_deterministic`
- The `_RECORD_DEFERRED_MSG` constant (dead since Task 4)

Rewrite `_allowed_hosts_for_candidate` and `_score_report` to consume a `sources_map: Mapping[str, list[str]]` instead of an `_EvalCorpus | None` (P18 — preserves the union semantics across both modes):

```python
def _allowed_hosts_for_candidate(
    candidate_id: str, sources_map: Mapping[str, list[str]]
) -> set[str]:
    hosts: set[str] = set(_FIXED_HOST_ALLOWLIST)
    for url in sources_map.get(candidate_id, ()):
        host = urlparse(url).hostname
        if host is not None:
            hosts.add(host)
    return hosts


def _score_synthesis(s: Synthesis, *, sources_map: Mapping[str, list[str]]) -> dict[str, bool]:
    allowed = _allowed_hosts_for_candidate(s.candidate_id, sources_map)
    return {
        "where_diverged_nonempty": where_diverged_nonempty(s),
        "all_sources_in_allowed_domains": all_sources_in_allowed_domains(s, allowed),
        "lifespan_months_positive": lifespan_months_positive(s),
    }


def _score_report(
    report: Report, *, sources_map: Mapping[str, list[str]]
) -> dict[str, object]:
    assertions: dict[str, dict[str, bool]] = {}
    for s in report.candidates:
        assertions[s.candidate_id] = _score_synthesis(s, sources_map=sources_map)
    return {"candidates_count": len(report.candidates), "assertions": assertions}
```

Also update `_run_live` to build a `sources_map` from `corpus.lookup_sources` per candidate, lifting the previous live-mode strictness reduction:

```python
sources_map = {
    s.candidate_id: await corpus.lookup_sources(s.candidate_id)
    for s in report.candidates
}
results[rid] = _score_report(report, sources_map=sources_map)
```

Update the module docstring's "Modes" block: replace the deterministic-mode bullet with a cassette-mode bullet that points at `tests/fixtures/cassettes/evals/`. Remove the "Live-mode limitation" note about allowed_hosts collapsing — no longer applies (both modes use `lookup_sources`).

- [ ] **Step 6: Fix the justfile comment on line 19**

Modify `justfile:19`. Old comment was a lie until now; flip it to truth:

```just
# Default eval runs against committed cassettes via FakeLLMClient + FakeEmbeddingClient
# + an ephemeral Qdrant collection seeded from corpus_fixture.jsonl. No live API calls.
eval:
    uv run python -m slopmortem.evals.runner --dataset tests/evals/datasets/seed.jsonl --baseline tests/evals/baseline.json
```

- [ ] **Step 7: Run the runner-replay tests**

Run: `docker compose up -d qdrant && uv run pytest tests/evals/test_runner_replay.py -v -m requires_qdrant`
Expected: PASS (assuming Task 5's cassettes are committed, OR the tests use `tests/evals/test_fixtures/tiny_corpus.jsonl` + the hand-built cassettes added in Task 6).

- [ ] **Step 8: Run `just eval` against the committed fixtures**

Run: `docker compose up -d qdrant && just eval`
Expected: every row prints `PASS`, no regressions, exit 0.

- [ ] **Step 9: Run full suite + typecheck**

Run: `just test && just typecheck`
Expected: green.

---

## Task 7: Migrate `test_full_pipeline_with_fake_clients` to cassettes

Owner: one subagent. Depends on Task 6 + an operator-recorded cassette dir at `tests/fixtures/cassettes/e2e/test_full_pipeline_with_fake_clients/`.

**Files:**
- Modify: `tests/test_pipeline_e2e.py:200-290` (migrate `test_full_pipeline_with_fake_clients`)

The other two tests in that file (`test_run_query_records_budget_exceeded`, `test_ctrl_c_cancels_in_flight`) keep their canned `FakeLLMClient` setups — they're plumbing tests, not realism tests.

**Operator pre-step (before this task can land):** run `record_cassettes_for_inputs()` against the same `InputContext` the test currently feeds, writing cassettes to `tests/fixtures/cassettes/e2e/test_full_pipeline_with_fake_clients/`. Commit those cassettes.

**Assertions to port — explicit enumeration (P7 resolution).** The original test
asserted 12+ contract properties (`tests/test_pipeline_e2e.py:259-290`). The
migration MUST port them — silently dropping them via a placeholder comment
turns a contract test into a smoke test.

**Group A — port verbatim, no changes:**
1. `report.input == ctx` (original: line ~262) — structural equality
2. `0 < len(report.candidates) <= cfg.N_synthesize` (line ~263) — assertion holds because cassettes capture the same N candidates the original FakeLLMClient produced
3. `meta.K_retrieve == cfg.K_retrieve` — config round-trip
4. `meta.cost_usd_total == budget.spent_usd` — both sides see the same aggregate; cassette JSON includes `cost_usd` (spec line 216), `CompletionResult.cost_usd` survives replay
5. `meta.budget_exceeded is False`
6. `meta.trace_id is None`
7. `set(meta.models.keys()) == {"facet", "rerank", "synthesize"}`
8. `meta.models["facet"] == cfg.model_facet` (and `rerank`, `synthesize`) — exact strings
9. `"facet_extract" in progress_events` — span emission, LLM-implementation-agnostic

**Group B — port via `_CountingCorpus` test wrapper (3 assertions):**
- `len(corpus_observer.queries) == 1`
- `corpus_observer.queries[0]["k_retrieve"] == cfg.K_retrieve`
- `corpus_observer.queries[0]["strict_deaths"] == ctx.strict_deaths`

`_FakeCorpus.queries` is gone (replaced by ephemeral `QdrantCorpus`). Add a
~10-line wrapper that implements the `Corpus` Protocol, delegates to the
wrapped `QdrantCorpus`, and records each `query()` call's args. No production
change — `Corpus` is a Protocol so the wrapper composes transparently.

- [ ] **Step 1: Migrate `test_full_pipeline_with_fake_clients`**

Replace the body of that single test:

```python
@pytest.mark.requires_qdrant
async def test_full_pipeline_with_fake_clients() -> None:
    """End-to-end run against committed cassettes + ephemeral Qdrant."""
    from slopmortem.evals.cassettes import (
        load_embedding_cassettes,
        load_llm_cassettes,
        NoCannedEmbeddingError,
    )
    from slopmortem.evals.qdrant_setup import setup_ephemeral_qdrant
    from slopmortem.llm.cassettes import embed_cassette_key
    from slopmortem.llm.fake_embeddings import FakeEmbeddingClient

    class _CountingCorpus:
        """Test-only `Corpus` wrapper: records each query() call's kwargs."""
        def __init__(self, inner) -> None:
            self._inner = inner
            self.queries: list[dict[str, object]] = []
        async def query(self, *, dense, sparse, k_retrieve, strict_deaths, **kw):
            self.queries.append({"k_retrieve": k_retrieve, "strict_deaths": strict_deaths})
            return await self._inner.query(
                dense=dense, sparse=sparse, k_retrieve=k_retrieve,
                strict_deaths=strict_deaths, **kw,
            )
        # Forward any other Corpus methods the pipeline calls.
        def __getattr__(self, name):
            return getattr(self._inner, name)

    scope = Path("tests/fixtures/cassettes/e2e/test_full_pipeline_with_fake_clients")
    fixture = Path("tests/fixtures/corpus_fixture.jsonl")
    cfg = Config()  # default fastembed
    llm_canned = {
        k: FakeResponse(text=v.text, stop_reason=v.stop_reason, cost_usd=v.cost_usd)
        for k, v in load_llm_cassettes(scope).items()
    }
    dense_canned, sparse_canned = load_embedding_cassettes(scope)
    fake_llm = FakeLLMClient(canned=llm_canned, default_model=cfg.model_synthesize)
    fake_embed = FakeEmbeddingClient(model=cfg.embed_model_id, canned=dense_canned)

    def cassette_sparse(text: str) -> dict[int, float]:
        key = embed_cassette_key(text=text, model="Qdrant/bm25")
        if key not in sparse_canned:
            raise NoCannedEmbeddingError(f"no sparse cassette for {key!r}")
        idx, vals = sparse_canned[key]
        return dict(zip(idx, vals, strict=True))

    ctx = InputContext(name="acme", description="...the test's existing pitch text...")
    budget = Budget(cap_usd=2.0)
    progress_events: list[str] = []  # populated via the existing observe hook
    async with setup_ephemeral_qdrant(fixture) as qdrant_corpus:
        counting_corpus = _CountingCorpus(qdrant_corpus)
        report = await run_query(
            ctx, llm=fake_llm, embedding_client=fake_embed, corpus=counting_corpus,
            config=cfg, budget=budget, sparse_encoder=cassette_sparse,
        )

    # Group A — original contract assertions, ported verbatim:
    assert report.input == ctx
    assert 0 < len(report.candidates) <= cfg.N_synthesize
    meta = report.meta
    assert meta.K_retrieve == cfg.K_retrieve
    assert meta.cost_usd_total == budget.spent_usd
    assert meta.budget_exceeded is False
    assert meta.trace_id is None
    assert set(meta.models.keys()) == {"facet", "rerank", "synthesize"}
    assert meta.models["facet"] == cfg.model_facet
    assert meta.models["rerank"] == cfg.model_rerank
    assert meta.models["synthesize"] == cfg.model_synthesize
    assert "facet_extract" in progress_events

    # Group B — query-introspection assertions via _CountingCorpus:
    assert len(counting_corpus.queries) == 1
    q = counting_corpus.queries[0]
    assert q["k_retrieve"] == cfg.K_retrieve
    assert q["strict_deaths"] == ctx.strict_deaths
```

Note: the exact `_CountingCorpus.query()` signature must mirror the live
`QdrantCorpus.query()` keyword arguments — verify against
`slopmortem/corpus/qdrant_store.py` at implementation time. The
`progress_events` plumbing is the same observe hook the original test used
(`tests/test_pipeline_e2e.py:200-258`); port it verbatim.

- [ ] **Step 2: Run the migrated test**

Run: `docker compose up -d qdrant && uv run pytest tests/test_pipeline_e2e.py::test_full_pipeline_with_fake_clients -v -m requires_qdrant`
Expected: PASS.

- [ ] **Step 3: Run full suite + typecheck**

Run: `just test && just typecheck`
Expected: green.

---

## Task 8: Documentation

Owner: one subagent.

**Files:**
- Modify: `slopmortem/evals/runner.py` (module docstring's "Modes" list)
- Create: `docs/cassettes.md` (cassette author guide; per the spec's three-layer surface)
- Create: `tests/fixtures/cassettes/custom/.gitkeep`

- [x] **Step 1: Update the runner module docstring**

In `slopmortem/evals/runner.py:1-71`, replace the "Modes" block to reflect cassette-default behavior:

```
Modes:
    DEFAULT (cassettes) — uses FakeLLMClient + FakeEmbeddingClient backed by
        committed cassettes under tests/fixtures/cassettes/evals/<row_id>/,
        plus an ephemeral Qdrant collection seeded from
        tests/fixtures/corpus_fixture.jsonl. No env vars beyond a running
        Qdrant. This is what `just eval` and CI run.
    --live — wires real production deps via slopmortem.cli._build_deps.
        Operator-invoked, out of CI scope. Costs real money.
    --record — re-record cassettes against the live API. Calls
        record_cassettes_for_inputs() with --max-cost-usd as the ceiling.
    --scope <row_id> — restrict record or replay to a single row.
    --write-baseline — write the current run's results to --baseline (v2
        envelope, merging into any existing v2).
```

- [x] **Step 2: Create `docs/cassettes.md`**

Author guide with the three-layer surface, the marketplace-scrap walkthrough, the LFS prerequisite, and the CI checkout note. The `agent` ownership for this is **modifying** an existing repo (per CLAUDE.md, do not write `*.md` unless explicitly asked — but the spec line 808-810 explicitly enumerates this file as a deliverable, so it's on-spec). Sections:

- Quick start (replay)
- Recording for the canonical eval
- Recording for a custom test (Layer 2 walkthrough)
- Cassette schema reference (point at `slopmortem/evals/cassettes.py`)
- Troubleshooting (`NoCannedResponseError`, `NoCannedEmbeddingError`, `corpus_fixture_sha256` mismatch, LFS pointer file)
- CI/onboarding (`git lfs install`, `actions/checkout@v4` with `lfs: true`)

- [x] **Step 3: Create `tests/fixtures/cassettes/custom/.gitkeep`**

Reserve the subtree for ad-hoc cassette sets.

- [x] **Step 4: Run lint + typecheck**

Run: `just lint && just typecheck && just test`
Expected: green.

---

## Spec consistency check (run before merging)

These greps assert the plan's invariants. All "MUST match" should resolve to expected hits; "MUST be empty" / "MUST not exist" should produce no output.

```bash
# Cassette schema version is "1.0" (P12 forward-compat policy)
grep -nE 'CASSETTE_SCHEMA_VERSION|schema_version' slopmortem/evals/recording.py slopmortem/evals/cassettes.py slopmortem/evals/recording_helper.py
grep -nE '_SCHEMA_MAJOR = 1|_SCHEMA_MINOR = 0' slopmortem/evals/cassettes.py    # MUST match

# Cassette key derivation lives under llm/, not evals/ (P17 fix)
test -e slopmortem/llm/cassettes.py                                     # MUST exist
grep -nE '^def (template_sha|llm_cassette_key|embed_cassette_key)' slopmortem/llm/cassettes.py    # MUST match
grep -nE 'from slopmortem\.evals import cassettes|from slopmortem\.evals\.cassettes import .* (llm_cassette_key|embed_cassette_key|template_sha)' slopmortem/    # MUST be empty
grep -nE '# noqa: PLC0415' slopmortem/llm/fake.py                       # MUST be empty (no lazy import)

# Cassette loaders validate via Pydantic, not # pyright: ignore (P16 fix)
grep -nE '# pyright: ignore\[reportArgumentType\]' slopmortem/evals/cassettes.py    # MUST be empty
grep -nE 'TypeAdapter' slopmortem/evals/cassettes.py                    # MUST match

# Corpus protocol exposes lookup_sources for eval scoring (P18 fix)
grep -nE 'lookup_sources' slopmortem/corpus/store.py slopmortem/corpus/qdrant_store.py slopmortem/evals/runner.py    # MUST match (3+ hits)

# Baseline schema version is 2 in the new write path
grep -nE '"version": 2|_BASELINE_VERSION = 2' slopmortem/evals/runner.py

# No truncated hash in filenames anywhere
grep -nrE 'prompt_hash\[.*:8\]|text_hash\[.*:8\]' slopmortem/ tests/    # MUST be empty

# No 2-tuple wildcard fallback in FakeLLMClient (B4)
grep -nE 'len\(key\) == 2|2-tuple|wildcard' slopmortem/llm/fake.py      # MUST be empty

# pipeline.run_query exposes sparse_encoder (B1)
grep -nE 'sparse_encoder' slopmortem/pipeline.py                        # MUST match

# Recording lives under evals/, not llm/ (G14)
test ! -e slopmortem/llm/recording.py                                   # MUST not exist
test -e slopmortem/evals/recording.py                                   # MUST exist

# Slugifier handles all non-[A-Za-z0-9._-] (F23)
grep -nE '_slugify_model|re\.sub.*\[\^A-Za-z0-9' slopmortem/evals/cassettes.py

# tmp_dir uses pid+uuid suffix (F21)
grep -nE '\.recording.*\{.*pid.*uuid|uuid4\(\)\.hex.*recording' slopmortem/evals/recording_helper.py

# Sparse cassette uses Qdrant/bm25 model id
grep -nE '"Qdrant/bm25"' slopmortem/evals/recording.py slopmortem/evals/cassettes.py

# baseline.json recording_metadata captures embedding_provider
grep -nE '"embedding_provider"|embedding_provider' slopmortem/evals/runner.py    # MUST match

# runner reads dense embed model from Config, not a hardcoded literal
grep -nE '_DETERMINISTIC_EMBED_MODEL' slopmortem/evals/runner.py        # MUST be empty
```
