"""Typer CLI entrypoint. Subcommands registered by side-effect import."""

from __future__ import annotations

from slopmortem.cli._app import app as app

__all__ = ["app"]
