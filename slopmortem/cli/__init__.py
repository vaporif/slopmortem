"""Typer CLI entrypoint. Subcommands registered by side-effect import."""

from __future__ import annotations

from slopmortem.cli._app import app as app

# Side-effect imports: each module registers its @app.command() handler.
# Order determines `--help` listing order.
from slopmortem.cli import _ingest_cmd  # noqa: F401

__all__ = ["app"]
