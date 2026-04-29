"""--dry-run: count entries that would be ingested, write nothing."""

from datetime import UTC, datetime

import pytest

from slopmortem.budget import Budget
from slopmortem.config import Config
from slopmortem.corpus.merge import MergeJournal
from slopmortem.ingest import FakeSlopClassifier, InMemoryCorpus, ingest
from slopmortem.llm.fake import FakeLLMClient, FakeResponse
from slopmortem.llm.fake_embeddings import FakeEmbeddingClient
from slopmortem.models import RawEntry

_HAIKU = "anthropic/claude-haiku-4.5"


def _stub_sparse(_text: str) -> dict[int, float]:
    return {0: 1.0}


def _canned() -> dict[tuple[str, str, str], FakeResponse]:
    # dry_run exits before any LLM call, so the canned dict is unused at runtime.
    # Empty is valid; we keep the function for parity with the non-dry-run files.
    return {}


def _entry(i: int) -> RawEntry:
    return RawEntry(
        source="curated",
        source_id=str(i),
        url=f"https://e{i}.com",
        raw_html=None,
        markdown_text=f"body {i} " * 100,
        fetched_at=datetime(2026, 4, 28, tzinfo=UTC),
    )


class _ListSource:
    def __init__(self, n: int) -> None:
        self.n = n

    async def fetch(self):
        for i in range(self.n):
            yield _entry(i)


@pytest.fixture
def cfg() -> Config:
    return Config(max_cost_usd_per_ingest=100.0, ingest_concurrency=20)


async def test_dry_run_counts_but_writes_nothing(tmp_path, cfg):
    journal = MergeJournal(tmp_path / "j.sqlite")
    await journal.init()
    corpus = InMemoryCorpus()
    llm = FakeLLMClient(canned=_canned(), default_model=_HAIKU)
    embed = FakeEmbeddingClient(model=cfg.embed_model_id)
    budget = Budget(cap_usd=cfg.max_cost_usd_per_ingest)
    classifier = FakeSlopClassifier(default_score=0.0)
    root = tmp_path / "post_mortems"

    n_entries = 4
    result = await ingest(
        sources=[_ListSource(n_entries)],
        enrichers=[],
        journal=journal,
        corpus=corpus,
        llm=llm,
        embed_client=embed,
        budget=budget,
        slop_classifier=classifier,
        config=cfg,
        post_mortems_root=root,
        dry_run=True,
        sparse_encoder=_stub_sparse,
    )
    assert result.dry_run is True
    assert result.would_process == n_entries
    assert result.processed == 0
    # Nothing in journal.
    assert await journal.fetch_all() == []
    assert await journal.fetch_quarantined() == []
    # Nothing in corpus.
    assert corpus.points == []
    # Nothing on disk.
    raw_root = root / "raw"
    canonical_root = root / "canonical"
    assert not raw_root.exists() or not any(raw_root.rglob("*.md"))
    assert not canonical_root.exists() or not any(canonical_root.rglob("*.md"))
