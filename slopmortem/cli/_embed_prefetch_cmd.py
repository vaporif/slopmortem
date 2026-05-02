"""``slopmortem embed-prefetch`` subcommand.

Only the local fastembed provider has anything to prefetch; remote providers
exit 1 with a one-line message.
"""

from __future__ import annotations

import anyio
import typer

from slopmortem.budget import Budget
from slopmortem.cli import app
from slopmortem.config import load_config
from slopmortem.llm import FastEmbedEmbeddingClient, make_embedder


@app.command("embed-prefetch")
def embed_prefetch_cmd() -> None:
    """Warm the configured embedder's model cache (useful for CI / first-run)."""
    anyio.run(_embed_prefetch)


async def _embed_prefetch() -> None:
    config = load_config()
    budget = Budget(cap_usd=0.0)
    embedder = make_embedder(config, budget)
    if not isinstance(embedder, FastEmbedEmbeddingClient):
        typer.echo(
            f"slopmortem: provider {config.embedding_provider!r} has no local cache to prefetch",
            err=True,
        )
        raise typer.Exit(code=1)
    try:
        await embedder.prefetch()
    except Exception as exc:
        typer.echo(f"slopmortem: embed-prefetch failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"slopmortem: prefetched {config.embed_model_id} into the fastembed cache")
