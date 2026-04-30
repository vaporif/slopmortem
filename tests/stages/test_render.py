"""Tests for ``slopmortem.render``: pure markdown emit, autolink/image stripping, no I/O."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from slopmortem.models import (
    InputContext,
    PerspectiveScore,
    PipelineMeta,
    Report,
    SimilarityScores,
    Synthesis,
)
from slopmortem.render import render

if TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


def _scores(value: float = 5.0) -> SimilarityScores:
    return SimilarityScores(
        business_model=PerspectiveScore(score=value, rationale="bm rationale"),
        market=PerspectiveScore(score=value, rationale="market rationale"),
        gtm=PerspectiveScore(score=value, rationale="gtm rationale"),
        stage_scale=PerspectiveScore(score=value, rationale="stage rationale"),
    )


def _synthesis_clean() -> Synthesis:
    return Synthesis(
        candidate_id="acme-corp",
        name="Acme",
        one_liner="B2B fintech for SMB invoicing.",
        failure_date=date(2023, 1, 1),
        lifespan_months=60,
        similarity=_scores(7.0),
        why_similar="Both target SMB invoicing.",
        where_diverged="New pitch is web-first; Acme was mobile-only.",
        failure_causes=["CAC > LTV", "long sales cycles"],
        lessons_for_input=["target larger ACVs", "avoid SMB churn"],
        sources=["https://acme.com/postmortem", "https://news.ycombinator.com/item?id=1"],
    )


def _synthesis_with_attacker_links() -> Synthesis:
    """Synthesis whose prose deliberately includes inline links and an image.

    The renderer must strip them out before they reach markdown — otherwise
    a terminal/markdown viewer would render them as one-click attacker URLs
    or as an exfil pixel.
    """
    return Synthesis(
        candidate_id="beta-co",
        name="BetaCo",
        one_liner="A/B-test SaaS for marketers.",
        failure_date=None,
        lifespan_months=None,
        similarity=_scores(4.0),
        why_similar="They served the same SMB cohort.",
        where_diverged=(
            "BetaCo was acquired in 2021. See [click here](https://attacker.com) "
            "and ![pwn](https://attacker.com/x.png) for details. "
            "Reference-style: [docs][ref]."
        ),
        failure_causes=["pivoted too late"],
        lessons_for_input=["[click](https://attacker.com)", "be honest"],
        sources=["https://betaco.example/blog"],
    )


def _report() -> Report:
    return Report(
        input=InputContext(name="newco", description="A B2B fintech for SMB invoicing"),
        generated_at=datetime(2026, 4, 28, 12, 0, 0, tzinfo=UTC),
        candidates=[_synthesis_clean(), _synthesis_with_attacker_links()],
        pipeline_meta=PipelineMeta(
            K_retrieve=30,
            N_synthesize=5,
            models={"rerank": "anthropic/claude-sonnet-4.6"},
            cost_usd_total=0.42,
            latency_ms_total=1234,
            trace_id="trace-abc",
            budget_remaining_usd=1.58,
            budget_exceeded=False,
        ),
    )


def _structural_keys(md: str) -> list[str]:
    """Extract heading lines and labelled fields so the snapshot is structural-only."""
    keys: list[str] = []
    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or re.match(r"^[A-Z][A-Za-z _-]+:\s*$", stripped):
            keys.append(stripped)
    return keys


def test_render_strips_autolinks_and_images(snapshot: SnapshotAssertion) -> None:
    md = render(_report())

    assert not re.search(r"\[[^\]]+\]\([^)]+\)", md)  # no inline links
    assert not re.search(r"\[[^\]]+\]\[[^\]]+\]", md)  # no reference-style links
    assert "![" not in md  # no image markdown

    assert _structural_keys(md) == snapshot


def test_render_emits_one_section_per_candidate() -> None:
    md = render(_report())
    # Two candidate names; the renderer puts each one in a level-2 heading.
    assert "## Acme" in md or "Acme" in md
    assert "BetaCo" in md
    # Footer block carries pipeline_meta.
    assert "trace-abc" in md
    assert "0.42" in md or "$0.42" in md
    assert "1234" in md


def test_render_keeps_sources_as_plain_text() -> None:
    md = render(_report())
    # Sources list URLs but does NOT wrap them in `[]()` markdown link syntax.
    assert "https://acme.com/postmortem" in md
    assert "https://news.ycombinator.com/item?id=1" in md
    # Defense-in-depth: even if a synthesis somehow contained an attacker URL
    # in `where_diverged` prose, the autolink stripper killed it.
    assert "[click here]" not in md
    assert "[click](" not in md


def test_render_is_pure_no_io() -> None:
    """``render`` must not touch the filesystem.

    Static check on the source — cheaper and more deterministic than trying
    to monkeypatch ``open``/``Path`` at runtime through syrupy's own I/O.
    """
    src = Path(__file__).resolve().parents[2] / "slopmortem" / "render.py"
    text = src.read_text()
    assert "open(" not in text, "render.py must not call open()"
    assert "Path(" not in text, "render.py must not construct Path objects"
