"""Typer CLI entrypoint. Subcommands registered by side-effect import."""

from __future__ import annotations

import os

# Silence gRPC C-Core's INFO log channel BEFORE Laminar pulls in grpcio.
# Without this, the OTLP exporter prints ``ev_poll_posix.cc:593 FD from fork
# parent still in poll list`` on every poll wake-up, which interleaves with
# Rich progress redraws and turns the terminal into a glog smear.
os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
os.environ.setdefault("GRPC_TRACE", "")
os.environ.setdefault("GLOG_minloglevel", "2")

# ``import typer`` MUST come after the GRPC env-var stanza — typer pulls in
# Laminar, which imports grpcio, and grpcio reads those vars at import time.
import typer

app = typer.Typer(
    add_completion=False,
    help="slopmortem: query and ingest startup post-mortems.",
)

# Side-effect imports register each module's @app.command() with ``app``.
# Registration order determines the ``--help`` listing order; ``# isort: split``
# markers prevent alphabetisation. Imports MUST come AFTER ``app`` is defined
# so each module's ``from slopmortem.cli import app`` resolves.
from slopmortem.cli import _ingest_cmd as _ingest_cmd  # noqa: E402

# isort: split
from slopmortem.cli import _query_cmd as _query_cmd  # noqa: E402

# isort: split
from slopmortem.cli import _replay_cmd as _replay_cmd  # noqa: E402

# isort: split
from slopmortem.cli import _embed_prefetch_cmd as _embed_prefetch_cmd  # noqa: E402

__all__ = ["app"]
