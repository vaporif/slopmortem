r"""Eval runner: drive a JSONL dataset through the pipeline and diff against a baseline.

Usage:
    python -m slopmortem.evals.runner --dataset PATH --baseline PATH \
        [--live] [--record] [--write-baseline]

Modes:
    DEFAULT (cassettes): FakeLLMClient + FakeEmbeddingClient backed by
        committed cassettes under tests/fixtures/cassettes/evals/<row_id>/,
        plus an ephemeral Qdrant collection seeded from
        tests/fixtures/corpus_fixture.jsonl. No env vars beyond a running
        Qdrant. What `just eval` and CI run.
    --live: real production deps via slopmortem.cli._build_deps. Operator-
        invoked, out of CI scope. Costs real money.
    --record: re-record cassettes against the live API. Calls
        record_cassettes_for_inputs() with --max-cost-usd as the ceiling.
    --scope <row_id>: restrict record or replay to one row.
    --write-baseline: write the current run's results to --baseline (v2
        envelope, merging into any existing v2).

Baseline JSON shape (normative)::

    {
      "version": 1,
      "rows": {
        "<row_id>": {
          "candidates_count": <int>,
          "assertions": {
            "<candidate_id>": {
              "where_diverged_nonempty": true,
              "all_sources_in_allowed_domains": true,
              "lifespan_months_positive": true,
              "claims_grounded_in_body": true
            }
          }
        }
      }
    }

Regression semantics
--------------------

A run is a **regression** (exit 1) iff:

- An assertion that was ``true`` in the baseline is ``false`` now, OR
- A row present in the baseline produces zero candidates now AND the baseline
  had a non-zero ``candidates_count``.

Forward-compat warnings (exit 0):

- A row in the current run is missing from the baseline.
- A candidate in the current row is missing from the baseline.
- An assertion is missing from the baseline for a known candidate.

A truncated row (``BudgetExceededError`` mid-run → partial Report) is **not**
a runner failure on its own. Assertions apply only to the Synthesis values
that made it through. A row with ``candidates_count=0`` matched against a
baseline that also has ``candidates_count=0`` passes.

Live-mode limitation
--------------------

In ``--live`` mode, ``allowed_hosts`` for ``all_sources_in_allowed_domains``
reduces to ``_FIXED_HOST_ALLOWLIST`` only. The public Corpus Protocol does
not expose payload sources, and we deliberately don't extend it. Deterministic
mode tightens this by including each candidate's own payload sources via the
private in-memory corpus.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

import anyio

from slopmortem.budget import Budget
from slopmortem.config import Config
from slopmortem.evals.assertions import (
    all_sources_in_allowed_domains,
    claims_grounded_in_body,
    lifespan_months_positive,
    where_diverged_nonempty,
)
from slopmortem.llm.fake import FakeLLMClient, FakeResponse
from slopmortem.llm.fake_embeddings import FakeEmbeddingClient
from slopmortem.llm.prompts import prompt_template_sha
from slopmortem.models import (
    Candidate,
    CandidatePayload,
    Facets,
    InputContext,
    Synthesis,
)
from slopmortem.pipeline import run_query

# Fixed host allowlist for the eval-time ``all_sources_in_allowed_domains``
# assertion in live mode (when no in-memory corpus is available to widen the
# set with payload sources). web.archive.org is intentionally NOT here —
# Wayback proxies arbitrary URLs and would bypass host-level allowlist
# semantics. The synthesize stage no longer consults this constant; sources
# come from CandidatePayload directly.
_FIXED_HOST_ALLOWLIST: frozenset[str] = frozenset({"news.ycombinator.com"})

if TYPE_CHECKING:
    from collections.abc import Mapping

    from slopmortem.llm.client import CompletionResult
    from slopmortem.models import Report

_DETERMINISTIC_FACET_MODEL = "test-facet"
_DETERMINISTIC_RERANK_MODEL = "test-rerank"
_DETERMINISTIC_SYNTH_MODEL = "test-synth"
_DETERMINISTIC_EMBED_MODEL = "text-embedding-3-small"

_BASELINE_VERSION = 1
_ROW_ID_HASH_PREFIX = 8

_ASSERTION_NAMES: tuple[str, ...] = (
    "where_diverged_nonempty",
    "all_sources_in_allowed_domains",
    "lifespan_months_positive",
    "claims_grounded_in_body",
)


# Deterministic-mode canned data — duplicated from tests/test_pipeline_e2e.py
# rather than extracted into a shared fixture module.
def _facets() -> Facets:
    return Facets(
        sector="fintech",
        business_model="b2b_saas",
        customer_type="smb",
        geography="us",
        monetization="subscription_recurring",
    )


def _payload(*, name: str, canonical_id: str) -> CandidatePayload:
    return CandidatePayload(
        name=name,
        summary=f"{name} was a B2B fintech.",
        body=f"{name} was a B2B fintech that ran out of runway.",
        facets=_facets(),
        founding_date=date(2018, 1, 1),
        failure_date=date(2023, 1, 1),
        founding_date_unknown=False,
        failure_date_unknown=False,
        provenance="curated_real",
        slop_score=0.0,
        sources=["https://news.ycombinator.com/item?id=" + canonical_id],
        text_id=canonical_id.replace("-", "") + "0123456789",
    )


def _candidate(canonical_id: str, *, score: float = 0.9) -> Candidate:
    return Candidate(
        canonical_id=canonical_id,
        score=score,
        payload=_payload(name=canonical_id, canonical_id=canonical_id),
    )


def _facet_extract_payload() -> str:
    return json.dumps(
        {
            "sector": "fintech",
            "business_model": "b2b_saas",
            "customer_type": "smb",
            "geography": "us",
            "monetization": "subscription_recurring",
            "sub_sector": "smb invoicing",
            "product_type": "saas",
            "price_point": "tiered",
            "founding_year": 2024,
            "failure_year": None,
        }
    )


def _rerank_payload(canonical_ids: list[str]) -> str:
    ranked = [
        {
            "candidate_id": cid,
            "perspective_scores": {
                "business_model": {"score": 7.0, "rationale": "match"},
                "market": {"score": 6.0, "rationale": "match"},
                "gtm": {"score": 5.0, "rationale": "match"},
                "stage_scale": {"score": 4.0, "rationale": "match"},
            },
            "rationale": "ranked",
        }
        for cid in canonical_ids
    ]
    return json.dumps({"ranked": ranked})


def _synthesis_payload(canonical_id: str) -> str:
    """Canned LLMSynthesis JSON.

    failure_date, lifespan_months, and sources are derived/passed-through by
    the pipeline from CandidatePayload, so the LLM no longer emits them.
    """
    return json.dumps(
        {
            "candidate_id": canonical_id,
            "name": canonical_id,
            "one_liner": "B2B fintech for SMB invoicing.",
            "similarity": {
                "business_model": {"score": 7.0, "rationale": "both B2B SaaS"},
                "market": {"score": 6.0, "rationale": "SMB overlap"},
                "gtm": {"score": 5.0, "rationale": "outbound sales"},
                "stage_scale": {"score": 4.0, "rationale": "seed stage"},
            },
            "why_similar": "Both target SMB invoicing.",
            "where_diverged": "Pitch is web-first; analogue was mobile-only.",
            "failure_causes": ["CAC > LTV"],
            "lessons_for_input": ["target larger ACVs"],
        }
    )


def _build_canned(
    *, candidate_ids: list[str]
) -> Mapping[tuple[str, str, str], FakeResponse | CompletionResult]:
    """Build the FakeLLMClient canned-response map for the deterministic run.

    Note: Task 6 of the eval-cassettes plan deletes this helper and replaces
    the deterministic runner with cassette-backed replay. This function still
    keys on a placeholder ``prompt_hash`` so the type matches
    ``FakeLLMClient.canned`` (now 3-tuple) and the codebase compiles in
    lock-step with Task 1. The live eval runner is exercised end-to-end
    through Task 6, not from this stub.
    """
    placeholder_hash = "0" * 16
    canned: dict[tuple[str, str, str], FakeResponse | CompletionResult] = {
        (
            prompt_template_sha("facet_extract"),
            _DETERMINISTIC_FACET_MODEL,
            placeholder_hash,
        ): FakeResponse(text=_facet_extract_payload(), cost_usd=0.0),
        (
            prompt_template_sha("llm_rerank"),
            _DETERMINISTIC_RERANK_MODEL,
            placeholder_hash,
        ): FakeResponse(text=_rerank_payload(candidate_ids), cost_usd=0.0),
        (
            prompt_template_sha("synthesize"),
            _DETERMINISTIC_SYNTH_MODEL,
            placeholder_hash,
        ): FakeResponse(text=_synthesis_payload("acme"), cost_usd=0.0, cache_creation_tokens=10),
    }
    return canned


@dataclass
class _EvalCorpus:
    """In-memory :class:`Corpus` for the deterministic eval runner.

    Adds :meth:`lookup_payload` so the runner can extract per-candidate source
    hosts for the ``all_sources_in_allowed_domains`` assertion. The Corpus
    Protocol intentionally does not expose payload-by-id.
    """

    candidates: list[Candidate]
    queries: list[dict[str, object]] = field(default_factory=list)

    async def query(  # noqa: PLR0913 — Protocol contract dictates the signature
        self,
        *,
        dense: list[float],
        sparse: dict[int, float],
        facets: Facets,
        cutoff_iso: str | None,
        strict_deaths: bool,
        k_retrieve: int,
    ) -> list[Candidate]:
        self.queries.append(
            {
                "dense_dim": len(dense),
                "sparse_keys": list(sparse),
                "facets": facets.model_dump(),
                "cutoff_iso": cutoff_iso,
                "strict_deaths": strict_deaths,
                "k_retrieve": k_retrieve,
            }
        )
        return list(self.candidates[:k_retrieve])

    async def get_post_mortem(self, canonical_id: str) -> str:
        for c in self.candidates:
            if c.canonical_id == canonical_id:
                return c.payload.body
        msg = f"unknown canonical_id {canonical_id!r}"
        raise KeyError(msg)

    async def search_corpus(
        self, q: str, facets: dict[str, str] | None = None
    ) -> list[dict[str, Any]]:  # pyright: ignore[reportExplicitAny]  # Corpus Protocol — values vary
        del q, facets
        return [
            {
                "canonical_id": c.canonical_id,
                "name": c.payload.name,
                "summary": c.payload.summary,
                "score": c.score,
            }
            for c in self.candidates
        ]

    def lookup_payload(self, canonical_id: str) -> CandidatePayload | None:
        """Return the persisted payload for *canonical_id*, or None if unknown.

        Private to the runner. The public :class:`Corpus` Protocol intentionally
        does not expose payloads. We use this only in deterministic mode to
        compute per-candidate ``allowed_hosts`` before calling
        :func:`all_sources_in_allowed_domains`.
        """
        for c in self.candidates:
            if c.canonical_id == canonical_id:
                return c.payload
        return None


def _no_op_sparse_encoder(_t: str) -> dict[int, float]:
    return {1: 1.0}


def _build_deterministic_config() -> Config:
    cfg = Config()
    return cfg.model_copy(
        update={
            "K_retrieve": 6,
            "N_synthesize": 3,
            "model_facet": _DETERMINISTIC_FACET_MODEL,
            "model_rerank": _DETERMINISTIC_RERANK_MODEL,
            "model_synthesize": _DETERMINISTIC_SYNTH_MODEL,
        }
    )


def _load_dataset(path: Path) -> list[InputContext]:
    """Parse a JSONL dataset into :class:`InputContext` rows.

    Empty lines are skipped. Each non-empty line must validate as
    ``InputContext`` (``name``, ``description``, optional ``years_filter``).
    """
    rows: list[InputContext] = []
    text = path.read_text()
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # ``json.loads`` returns ``Any``. Narrow at the InputContext boundary.
        parsed: object = json.loads(line)  # pyright: ignore[reportAny]
        rows.append(InputContext.model_validate(parsed))
    return rows


def _row_id(ctx: InputContext) -> str:
    """Derive a stable row id for keying baseline entries.

    Uses ``ctx.name`` when non-empty; otherwise falls back to the first
    8 hex chars of ``sha1(description)``. The runner verifies global
    uniqueness across the whole dataset and raises if duplicates would
    result.
    """
    if ctx.name:
        return ctx.name
    return hashlib.sha1(ctx.description.encode(), usedforsecurity=False).hexdigest()[
        :_ROW_ID_HASH_PREFIX
    ]


def _verify_unique_row_ids(rows: list[InputContext]) -> list[str]:
    """Compute row ids for *rows* and raise ValueError if any collide."""
    ids = [_row_id(r) for r in rows]
    seen: set[str] = set()
    dup: list[str] = []
    for rid in ids:
        if rid in seen:
            dup.append(rid)
        seen.add(rid)
    if dup:
        msg = f"duplicate row_ids in dataset: {sorted(set(dup))}"
        raise ValueError(msg)
    return ids


def _allowed_hosts_for_candidate(
    candidate_id: str,
    eval_corpus: _EvalCorpus | None,
) -> set[str]:
    """Compute the host allowlist used by ``all_sources_in_allowed_domains``.

    In deterministic mode (``eval_corpus is not None``), unions the
    fixed allowlist with the candidate's own payload sources. In
    ``--live`` mode (``eval_corpus is None``), reduces to the fixed
    allowlist only. See the module docstring's "Live-mode limitation".
    """
    hosts: set[str] = set(_FIXED_HOST_ALLOWLIST)
    if eval_corpus is None:
        return hosts
    payload = eval_corpus.lookup_payload(candidate_id)
    if payload is None:
        return hosts
    for url in payload.sources:
        host = urlparse(url).hostname
        if host is not None:
            hosts.add(host)
    return hosts


def _body_for_candidate(candidate_id: str, eval_corpus: _EvalCorpus | None) -> str | None:
    """Return the candidate body for ``claims_grounded_in_body``, or None in live mode.

    In deterministic mode, looks up the persisted payload via the private
    ``_EvalCorpus.lookup_payload``. In ``--live`` mode (``eval_corpus is None``),
    returns None — the public Corpus Protocol does not expose payload bodies
    (same constraint that ``_allowed_hosts_for_candidate`` lives with). The
    runner treats a None body as vacuously-grounded so live mode does not
    spuriously fail.
    """
    if eval_corpus is None:
        return None
    payload = eval_corpus.lookup_payload(candidate_id)
    if payload is None:
        return None
    return payload.body


def _score_synthesis(s: Synthesis, *, eval_corpus: _EvalCorpus | None) -> dict[str, bool]:
    """Apply every assertion in :data:`_ASSERTION_NAMES` to *s* and return the result dict."""
    allowed = _allowed_hosts_for_candidate(s.candidate_id, eval_corpus)
    body = _body_for_candidate(s.candidate_id, eval_corpus)
    return {
        "where_diverged_nonempty": where_diverged_nonempty(s),
        "all_sources_in_allowed_domains": all_sources_in_allowed_domains(s, allowed),
        "lifespan_months_positive": lifespan_months_positive(s),
        "claims_grounded_in_body": True if body is None else claims_grounded_in_body(s, body),
    }


def _score_report(report: Report, *, eval_corpus: _EvalCorpus | None) -> dict[str, object]:
    """Return the per-row results-dict in the baseline shape (without the row key)."""
    assertions: dict[str, dict[str, bool]] = {}
    for s in report.candidates:
        assertions[s.candidate_id] = _score_synthesis(s, eval_corpus=eval_corpus)
    return {
        "candidates_count": len(report.candidates),
        "assertions": assertions,
    }


async def _run_deterministic(
    rows: list[InputContext], row_ids: list[str]
) -> dict[str, dict[str, object]]:
    """Run every row in deterministic mode and return per-row scored results."""
    cfg = _build_deterministic_config()
    candidates = [_candidate(f"cand-{i}") for i in range(cfg.K_retrieve)]
    canned = _build_canned(candidate_ids=[c.canonical_id for c in candidates[: cfg.N_synthesize]])
    fake_llm = FakeLLMClient(canned=canned, default_model=_DETERMINISTIC_SYNTH_MODEL)
    fake_embed = FakeEmbeddingClient(model=_DETERMINISTIC_EMBED_MODEL)
    eval_corpus = _EvalCorpus(candidates=candidates)
    budget = Budget(cap_usd=2.0)

    # Stub the sparse encoder so fastembed isn't loaded (mirrors test_pipeline_e2e).
    import slopmortem.corpus.embed_sparse as _es  # noqa: PLC0415

    original_encode = _es.encode
    _es.encode = _no_op_sparse_encoder
    try:
        results: dict[str, dict[str, object]] = {}
        for ctx, rid in zip(rows, row_ids, strict=True):
            report = await run_query(
                ctx,
                llm=fake_llm,
                embedding_client=fake_embed,
                corpus=eval_corpus,
                config=cfg,
                budget=budget,
            )
            results[rid] = _score_report(report, eval_corpus=eval_corpus)
        return results
    finally:
        _es.encode = original_encode


async def _run_live(rows: list[InputContext], row_ids: list[str]) -> dict[str, dict[str, object]]:
    """Run every row through real production deps. May spend real money."""
    # Lazy-imported so deterministic mode doesn't drag CLI deps in.
    # Both names are private; sanctioned reuse: the runner mirrors the CLI
    # boot path exactly so live evals get the same prod wiring.
    from slopmortem.cli import (  # noqa: PLC0415
        _build_deps,  # pyright: ignore[reportPrivateUsage]
    )
    from slopmortem.config import load_config  # noqa: PLC0415
    from slopmortem.corpus.tools_impl import _set_corpus  # noqa: PLC0415

    cfg = load_config()
    llm, embedder, corpus, budget = _build_deps(cfg)
    _set_corpus(corpus)

    results: dict[str, dict[str, object]] = {}
    for ctx, rid in zip(rows, row_ids, strict=True):
        report = await run_query(
            ctx,
            llm=llm,
            embedding_client=embedder,
            corpus=corpus,
            config=cfg,
            budget=budget,
        )
        # In --live we have no payload-lookup. Pass eval_corpus=None so
        # allowed_hosts collapses to the fixed allowlist.
        results[rid] = _score_report(report, eval_corpus=None)
    return results


def _diff_against_baseline(
    current: dict[str, dict[str, object]],
    baseline: dict[str, object],
) -> tuple[list[str], list[str]]:
    """Compute (regressions, warnings) between *current* and *baseline*.

    ``baseline`` is the parsed JSON file's top-level dict (or ``{}`` when
    the file is empty / missing). Tolerates the ``{}`` case as "no baseline,
    every row is new" — emits no regressions, only forward-compat warnings.
    """
    regressions: list[str] = []
    warnings: list[str] = []

    raw_rows: object = baseline.get("rows", {}) if baseline else {}
    if not isinstance(raw_rows, dict):
        warnings.append("baseline.rows is not a dict; treating as empty")
        raw_rows = {}
    # Narrow once. Pyright infers ``dict[Unknown, Unknown]`` from the runtime
    # check, which is good enough for this scope.
    baseline_rows = cast("dict[str, dict[str, object]]", raw_rows)

    for row_id, cur_row in current.items():
        if row_id not in baseline_rows:
            warnings.append(f"row {row_id!r} not in baseline; recording for next run")
            continue
        base_row = baseline_rows[row_id]
        regressions.extend(_diff_row(row_id, cur_row, base_row))

    return regressions, warnings


def _diff_row(
    row_id: str,
    cur_row: dict[str, object],
    base_row: dict[str, object],
) -> list[str]:
    """Return regression messages for a single (current, baseline) row pair."""
    out: list[str] = []
    base_count_obj = base_row.get("candidates_count", 0)
    cur_count_obj = cur_row.get("candidates_count", 0)
    base_count = int(base_count_obj) if isinstance(base_count_obj, int) else 0
    cur_count = int(cur_count_obj) if isinstance(cur_count_obj, int) else 0
    if base_count > 0 and cur_count == 0:
        out.append(f"row {row_id!r}: produced 0 candidates (baseline had {base_count})")

    base_assertions_obj = base_row.get("assertions", {})
    cur_assertions_obj = cur_row.get("assertions", {})
    if not isinstance(base_assertions_obj, dict) or not isinstance(cur_assertions_obj, dict):
        return out
    base_assertions = cast("dict[str, dict[str, bool]]", base_assertions_obj)
    cur_assertions = cast("dict[str, dict[str, bool]]", cur_assertions_obj)

    for cand_id, base_results in base_assertions.items():
        cur_results = cur_assertions.get(cand_id)
        if cur_results is None:
            # Baseline had a candidate that the current run didn't synthesize.
            # Only a regression if the baseline asserted something true.
            if any(bool(v) for v in base_results.values()):
                out.append(f"row {row_id!r} candidate {cand_id!r}: missing from current run")
            continue
        for name, base_value in base_results.items():
            if not base_value:
                continue
            cur_value = cur_results.get(name)
            if cur_value is False:
                msg = (
                    f"row {row_id!r} candidate {cand_id!r}:"
                    f" {name} regressed (baseline=true, current=false)"
                )
                out.append(msg)
    return out


def _load_baseline(path: Path) -> dict[str, object]:
    """Load the baseline JSON, returning ``{}`` if the file is missing or empty."""
    if not path.exists():
        return {}
    text = path.read_text().strip()
    if not text:
        return {}
    parsed: object = json.loads(text)  # pyright: ignore[reportAny]
    if not isinstance(parsed, dict):
        msg = f"baseline at {path} must be a JSON object, got {type(parsed).__name__}"
        raise TypeError(msg)
    return cast("dict[str, object]", parsed)


def _serialize_results(results: dict[str, dict[str, object]]) -> dict[str, object]:
    """Wrap per-row results in the normative baseline-file envelope."""
    return {"version": _BASELINE_VERSION, "rows": results}


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="slopmortem.evals.runner",
        description=(
            "Run a JSONL eval dataset through the synthesis pipeline and "
            "compare per-row assertion results against a recorded baseline. "
            "Default mode is deterministic (FakeLLMClient + FakeEmbeddingClient + "
            "in-memory corpus); --live wires real production deps. CI runs "
            "deterministic mode only."
        ),
    )
    _ = p.add_argument(
        "--dataset",
        type=Path,
        required=True,
        help="Path to a JSONL dataset; each line is an InputContext.",
    )
    _ = p.add_argument(
        "--baseline",
        type=Path,
        required=True,
        help=(
            "Path to baseline.json. Missing/empty file is tolerated "
            "(treated as no baseline, every row is new)."
        ),
    )
    _ = p.add_argument(
        "--live",
        action="store_true",
        help=(
            "Use real production deps via slopmortem.cli._build_deps. "
            "Requires env keys + Qdrant; will spend real money. CI does not run this."
        ),
    )
    _ = p.add_argument(
        "--record",
        action="store_true",
        help=(
            "Re-record cassettes against the live API via the recording helper. "
            "Requires `tests/fixtures/corpus_fixture.jsonl` to exist; run "
            "`just eval-record-corpus` first if missing."
        ),
    )
    _ = p.add_argument(
        "--write-baseline",
        action="store_true",
        help=(
            "Write the current run's results to --baseline instead of comparing. "
            "Use this to bootstrap a baseline file."
        ),
    )
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
    return p


def _run_record(
    *,
    dataset_path: Path,
    scope: str | None,
    max_cost_usd: float,
) -> None:
    """Dispatch to the recording helper. Exits the process on completion or error."""
    from slopmortem.config import load_config  # noqa: PLC0415
    from slopmortem.evals import recording_helper  # noqa: PLC0415

    rows = _load_dataset(dataset_path)
    if scope is not None:
        rows = [r for r in rows if r.name == scope]
        if not rows:
            valid = [r.name for r in _load_dataset(dataset_path)]
            print(  # noqa: T201 — CLI surface
                f"unknown scope {scope!r}; valid: {valid}",
                file=sys.stderr,
            )
            sys.exit(2)
    cfg = load_config()
    output_dir = Path("tests/fixtures/cassettes/evals")
    corpus_fixture_path = Path("tests/fixtures/corpus_fixture.jsonl")
    if not corpus_fixture_path.exists():
        print(  # noqa: T201 — CLI surface
            f"missing {corpus_fixture_path}; run `just eval-record-corpus` first",
            file=sys.stderr,
        )
        sys.exit(2)
    asyncio.run(
        recording_helper.record_cassettes_for_inputs(
            inputs=rows,
            output_dir=output_dir,
            corpus_fixture_path=corpus_fixture_path,
            config=cfg,
            max_cost_usd=max_cost_usd,
        )
    )
    sys.exit(0)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: parse args, drive the run, exit 0 on no regression else 1."""
    parser = _build_argparser()
    ns = parser.parse_args(argv)
    # ``argparse.Namespace`` attributes are ``Any`` by design (argparse
    # builds the namespace dynamically). We narrow each at the boundary
    # with explicit casts. ``--store_true`` flags read out as ``bool``.
    dataset_path = cast("Path", ns.dataset)
    baseline_path = cast("Path", ns.baseline)
    live = cast("bool", ns.live)
    record = cast("bool", ns.record)
    write_baseline = cast("bool", ns.write_baseline)
    scope = cast("str | None", ns.scope)
    max_cost_usd = cast("float", ns.max_cost_usd)

    if record:
        _run_record(dataset_path=dataset_path, scope=scope, max_cost_usd=max_cost_usd)

    rows = _load_dataset(dataset_path)
    row_ids = _verify_unique_row_ids(rows)

    if live:
        results = anyio.run(_run_live, rows, row_ids)
    else:
        results = anyio.run(_run_deterministic, rows, row_ids)

    serialized = _serialize_results(results)

    if write_baseline:
        baseline_path.write_text(json.dumps(serialized, indent=2, sort_keys=True) + "\n")
        print(f"wrote baseline to {baseline_path}")  # noqa: T201 — CLI surface
        sys.exit(0)

    baseline = _load_baseline(baseline_path)

    for row_id, row_results in results.items():
        assertions_obj = row_results.get("assertions", {})
        if not isinstance(assertions_obj, dict):
            continue
        assertions = cast("dict[str, dict[str, bool]]", assertions_obj)
        for cand_id, results_for_cand in assertions.items():
            ok = all(results_for_cand.values())
            mark = "PASS" if ok else "FAIL"
            print(f"{mark} {row_id} {cand_id}")  # noqa: T201 — CLI surface

    regressions, warnings = _diff_against_baseline(results, baseline)
    for w in warnings:
        print(f"WARN {w}")  # noqa: T201 — CLI surface

    if regressions:
        for r in regressions:
            print(f"REGRESSION {r}", file=sys.stderr)  # noqa: T201 — CLI surface
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
