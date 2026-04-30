from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JUST = shutil.which("just")

pytestmark = pytest.mark.skipif(JUST is None, reason="just binary not available")


def _run_init_env(cwd: Path, stdin: str) -> subprocess.CompletedProcess[str]:
    assert JUST is not None
    return subprocess.run(  # noqa: S603  # JUST resolved via shutil.which; static argv
        [JUST, "init-env"],
        cwd=cwd,
        input=stdin,
        text=True,
        check=True,
        capture_output=True,
        timeout=30,
    )


def test_init_env_writes_required_key_and_skips_blank_optional(tmp_path: Path):
    shutil.copy(ROOT / "justfile", tmp_path / "justfile")
    _run_init_env(tmp_path, "or-key\n\n\n\n")
    env = (tmp_path / ".env").read_text()
    assert "OPENROUTER_API_KEY=or-key" in env
    assert "TAVILY_API_KEY=" not in env
    assert "OPENAI_API_KEY=" not in env
    assert "LMNR_PROJECT_API_KEY=" not in env


def test_init_env_preserves_existing_value_on_blank_input(tmp_path: Path):
    shutil.copy(ROOT / "justfile", tmp_path / "justfile")
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=keep-me\n")
    _run_init_env(tmp_path, "\n\n\n\n")
    assert "OPENROUTER_API_KEY=keep-me" in (tmp_path / ".env").read_text()


def test_init_env_overwrites_existing_value_when_provided(tmp_path: Path):
    shutil.copy(ROOT / "justfile", tmp_path / "justfile")
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=old-value\n")
    _run_init_env(tmp_path, "new-value\n\n\n\n")
    env = (tmp_path / ".env").read_text()
    assert "OPENROUTER_API_KEY=new-value" in env
    assert "old-value" not in env
