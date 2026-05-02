"""CliRunner tests for the ``slopmortem embed-prefetch`` subcommand."""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from slopmortem.budget import Budget
from slopmortem.cli import app
from slopmortem.llm import FastEmbedEmbeddingClient

if TYPE_CHECKING:
    import pytest

    from slopmortem.config import Config


def _stub_fastembed_client(*, load_sync: object) -> FastEmbedEmbeddingClient:
    """Build a real FastEmbedEmbeddingClient whose model load is replaced."""
    client = FastEmbedEmbeddingClient(
        model="nomic-ai/nomic-embed-text-v1.5",
        budget=Budget(cap_usd=0.0),
    )
    client._load_sync = load_sync  # pyright: ignore[reportAttributeAccessIssue]
    return client


def test_embed_prefetch_success(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()

    def fake_make(_config: Config, _budget: Budget) -> FastEmbedEmbeddingClient:
        return _stub_fastembed_client(load_sync=lambda: sentinel)

    monkeypatch.setattr("slopmortem.cli._app.make_embedder", fake_make)
    result = CliRunner().invoke(app, ["embed-prefetch"])
    assert result.exit_code == 0, (result.stdout or "") + (result.stderr or "")
    assert "prefetched" in result.stdout


def test_embed_prefetch_non_fastembed_provider_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    class NotFastEmbed:
        """Stand-in for any non-fastembed embedder."""

    def fake_make(_config: Config, _budget: Budget) -> object:
        return NotFastEmbed()

    monkeypatch.setattr("slopmortem.cli._app.make_embedder", fake_make)
    result = CliRunner().invoke(app, ["embed-prefetch"])
    assert result.exit_code == 1
    assert "no local cache to prefetch" in (result.stderr or "")


def test_embed_prefetch_load_failure_exits_1(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> object:
        msg = "simulated load failure"
        raise RuntimeError(msg)

    def fake_make(_config: Config, _budget: Budget) -> FastEmbedEmbeddingClient:
        return _stub_fastembed_client(load_sync=boom)

    monkeypatch.setattr("slopmortem.cli._app.make_embedder", fake_make)
    result = CliRunner().invoke(app, ["embed-prefetch"])
    assert result.exit_code == 1
    assert "embed-prefetch failed" in (result.stderr or "")
