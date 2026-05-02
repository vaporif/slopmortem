"""Typer CLI entrypoint. Subcommands registered by side-effect import."""

from __future__ import annotations

import os

# Silence gRPC C-Core's INFO log channel BEFORE the Laminar import below pulls
# in grpcio. Without this, the OTLP exporter's pool prints
# ``ev_poll_posix.cc:593 FD from fork parent still in poll list`` on every
# poll-loop wake-up, which interleaves with the Rich progress redraws and turns
# the terminal into a glog smear. ``ERROR`` keeps real failures visible while
# killing the chatty INFO/WARNING traffic.
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("GRPC_TRACE", "")
os.environ.setdefault("GLOG_minloglevel", "2")

# ``import typer`` (and the subcommand modules below) MUST come after the GRPC
# env-var stanza above — they pull in Laminar, which transitively imports
# grpcio, and grpcio reads ``GRPC_VERBOSITY`` / ``GRPC_TRACE`` at import time.
import typer

app = typer.Typer(
    add_completion=False,
    help="slopmortem: query and ingest startup post-mortems.",
)

# Side-effect imports: each module registers its @app.command() handler with
# ``app``. Registration order determines the ``--help`` listing order. The
# ``import x as x`` alias form marks the imports as re-exports so basedpyright
# doesn't flag them as unused. ``# isort: split`` markers split the import
# block so ruff/isort can't alphabetize them. These imports MUST come AFTER
# ``app`` is defined so each module's ``from slopmortem.cli import app``
# resolves against the partially-initialised package.
from slopmortem.cli import _ingest_cmd as _ingest_cmd  # noqa: E402

# isort: split
from slopmortem.cli import _query_cmd as _query_cmd  # noqa: E402

# isort: split
from slopmortem.cli import _replay_cmd as _replay_cmd  # noqa: E402

# isort: split
from slopmortem.cli import _embed_prefetch_cmd as _embed_prefetch_cmd  # noqa: E402

__all__ = ["app"]
