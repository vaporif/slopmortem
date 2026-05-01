"""Cassette-replay integration tests for the eval runner.

Most tests need an ephemeral Qdrant collection. The unknown-scope test exits
before any Qdrant call and runs without the marker so a Qdrant-less host can
still verify the validation gate.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from slopmortem.evals import runner

if TYPE_CHECKING:
    from _pytest.capture import CaptureFixture


@pytest.mark.requires_qdrant
def test_runner_replay_passes_with_recorded_cassettes(tmp_path: Path) -> None:
    """Happy path: ephemeral Qdrant + committed cassette dir → exit 0, non-empty rows."""
    dataset = tmp_path / "seed.jsonl"
    dataset.write_text(
        json.dumps({"name": "ledgermint", "description": "B2B SaaS that automates monthly close."})
        + "\n"
    )
    baseline = tmp_path / "baseline.json"

    with pytest.raises(SystemExit) as excinfo:
        runner.main(
            [
                "--dataset",
                str(dataset),
                "--baseline",
                str(baseline),
                "--write-baseline",
            ]
        )
    assert excinfo.value.code == 0
    parsed = json.loads(baseline.read_text())
    assert parsed["version"] == 1
    assert "ledgermint" in parsed["rows"]
    row = parsed["rows"]["ledgermint"]
    assert row["candidates_count"] > 0, "happy path must exercise at least one candidate"
    assert row["assertions"], "assertions map must be non-empty on the happy path"


@pytest.mark.requires_qdrant
def test_runner_replay_fails_loud_on_missing_cassette_dir(
    tmp_path: Path, capsys: CaptureFixture[str]
) -> None:
    """Missing cassette dir → FAIL line printed AND candidates_count=0 in baseline."""
    dataset = tmp_path / "seed.jsonl"
    dataset.write_text(json.dumps({"name": "no-such-row", "description": "n/a"}) + "\n")
    baseline = tmp_path / "baseline.json"

    with pytest.raises(SystemExit) as excinfo:
        runner.main(
            [
                "--dataset",
                str(dataset),
                "--baseline",
                str(baseline),
                "--write-baseline",
            ]
        )
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "FAIL no-such-row" in out, "missing cassette dir must print a FAIL line"
    parsed = json.loads(baseline.read_text())
    assert parsed["rows"]["no-such-row"]["candidates_count"] == 0
    assert parsed["rows"]["no-such-row"]["assertions"] == {}


def test_runner_replay_unknown_scope_is_fatal(tmp_path: Path) -> None:
    """`--scope notarow` exits 2 because scope-validation runs before any Qdrant call.

    No requires_qdrant marker — the gate fires in main() before dispatch.
    """
    dataset = tmp_path / "seed.jsonl"
    dataset.write_text(json.dumps({"name": "ledgermint", "description": "n/a"}) + "\n")
    baseline = tmp_path / "baseline.json"

    with pytest.raises(SystemExit) as excinfo:
        runner.main(
            [
                "--dataset",
                str(dataset),
                "--baseline",
                str(baseline),
                "--scope",
                "notarow",
                "--write-baseline",
            ]
        )
    assert excinfo.value.code == 2


@pytest.mark.requires_qdrant
def test_runner_replay_fails_loud_on_llm_cassette_miss(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutating an LLM cassette filename → NoCannedResponseError → FAIL line, candidates_count=0.

    Strategy: copy the committed ledgermint cassette dir into a tmp scope dir,
    rewrite one synthesize__*.json filename to a deliberately-different prompt
    hash, then invoke the runner against the tmp dir via the
    ``runner._CASSETTE_ROOT`` monkeypatch hook.
    """
    src = Path("tests/fixtures/cassettes/evals/ledgermint")
    dst_root = tmp_path / "cassettes_root"
    dst = dst_root / "ledgermint"
    shutil.copytree(src, dst)
    synth = next(dst.glob("synthesize__*.json"))
    synth.rename(dst / "synthesize__deadbeefdeadbeef.json")

    dataset = tmp_path / "seed.jsonl"
    dataset.write_text(
        json.dumps({"name": "ledgermint", "description": "B2B SaaS that automates monthly close."})
        + "\n"
    )
    baseline = tmp_path / "baseline.json"
    monkeypatch.setattr(runner, "_CASSETTE_ROOT", dst_root)
    with pytest.raises(SystemExit) as excinfo:
        runner.main(["--dataset", str(dataset), "--baseline", str(baseline), "--write-baseline"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "FAIL ledgermint: cassette miss" in out
    parsed = json.loads(baseline.read_text())
    assert parsed["rows"]["ledgermint"]["candidates_count"] == 0


@pytest.mark.requires_qdrant
def test_runner_replay_malformed_cassette_is_run_level_failure(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cassette with garbage JSON → CassetteFormatError → exit 2 (run-level failure)."""
    del capsys  # unused here; the failure surfaces via exit code, not stdout
    src = Path("tests/fixtures/cassettes/evals/ledgermint")
    dst_root = tmp_path / "cassettes_root"
    dst = dst_root / "ledgermint"
    shutil.copytree(src, dst)
    next(dst.glob("synthesize__*.json")).write_text("{not valid json")

    dataset = tmp_path / "seed.jsonl"
    dataset.write_text(json.dumps({"name": "ledgermint", "description": "n/a"}) + "\n")
    baseline = tmp_path / "baseline.json"
    monkeypatch.setattr(runner, "_CASSETTE_ROOT", dst_root)
    with pytest.raises(SystemExit) as excinfo:
        runner.main(["--dataset", str(dataset), "--baseline", str(baseline), "--write-baseline"])
    assert excinfo.value.code == 2


@pytest.mark.requires_qdrant
def test_switching_embed_model_id_produces_loud_cassette_miss(
    tmp_path: Path, capsys: CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changing ``Config.embed_model_id`` between record and replay → cassette miss → FAIL."""
    monkeypatch.setenv("SLOPMORTEM_EMBED_MODEL_ID", "text-embedding-3-large")

    dataset = tmp_path / "seed.jsonl"
    dataset.write_text(json.dumps({"name": "ledgermint", "description": "n/a"}) + "\n")
    baseline = tmp_path / "baseline.json"

    with pytest.raises(SystemExit) as excinfo:
        runner.main(["--dataset", str(dataset), "--baseline", str(baseline), "--write-baseline"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "FAIL ledgermint" in out
