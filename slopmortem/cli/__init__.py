"""Typer CLI entrypoint. Subcommands registered by side-effect import."""

from __future__ import annotations

# Side-effect imports: each module registers its @app.command() handler with
# ``app``. Registration order determines the ``--help`` listing order. The
# ``import x as x`` alias form marks the imports as re-exports so basedpyright
# doesn't flag them as unused. ``# isort: split`` markers split the import
# block so ruff/isort can't alphabetize them.
from slopmortem.cli import _ingest_cmd as _ingest_cmd
from slopmortem.cli._app import app as app

# isort: split
from slopmortem.cli import _query_cmd as _query_cmd

# isort: split
from slopmortem.cli import _replay_cmd as _replay_cmd

# isort: split
from slopmortem.cli import _embed_prefetch_cmd as _embed_prefetch_cmd

__all__ = ["app"]
