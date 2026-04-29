"""CLI: regenerate `corpus_fixture.jsonl` by running real ingest then dumping."""

import argparse
import sys


def main(argv: list[str] | None = None) -> None:
    """Stub entry point for the corpus recorder CLI.

    Raises:
        SystemExit: Always exits with code 1 — the full implementation lands
            in commit 5 (operator-only).
    """
    p = argparse.ArgumentParser(prog="slopmortem.evals.corpus_recorder")
    _ = p.add_argument("--inputs", required=True)
    _ = p.add_argument("--out", required=True)
    _ = p.parse_args(argv)
    print(  # noqa: T201 — CLI surface
        "eval-record-corpus is operator-only; full implementation lands in commit 5",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
