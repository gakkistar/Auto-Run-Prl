import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import FAKE_RUNNER

PROJECT_ROOT = Path(__file__).parent.parent


def write_tokens(path: Path, ids: list[str]) -> Path:
    lines = "\n".join(f"{tid},secret-{tid}" for tid in ids)
    path.write_text(lines + "\n", encoding="utf-8")
    return path


def run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "modal_orchestrator", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_run_completes_with_fake_runner(tmp_path):
    tokens_file = write_tokens(tmp_path / "tokens.csv", ["ak-1", "ak-2"])
    state_file = tmp_path / "state.json"
    log_dir = tmp_path / "logs"

    res = run_cli(
        [
            "run",
            "--tokens", str(tokens_file),
            "--config", "dummy-config.py",
            "--state", str(state_file),
            "--logs", str(log_dir),
            "--cmd", sys.executable,
            "--cmd", str(FAKE_RUNNER),
            "--cmd", "{config}",
            "--max-parallel", "2",
        ],
        cwd=PROJECT_ROOT,
    )
    assert res.returncode == 0, res.stderr
    import json
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert all(t["status"] == "used_ok" for t in data["tokens"].values())
    assert len(data["tokens"]) == 2


def test_status_subcommand_prints_counts(tmp_path):
    tokens_file = write_tokens(tmp_path / "tokens.csv", ["ak-1", "ak-2"])
    state_file = tmp_path / "state.json"
    log_dir = tmp_path / "logs"

    run_cli(
        [
            "run",
            "--tokens", str(tokens_file),
            "--config", "c.py",
            "--state", str(state_file),
            "--logs", str(log_dir),
            "--cmd", sys.executable,
            "--cmd", str(FAKE_RUNNER),
            "--cmd", "{config}",
        ],
        cwd=PROJECT_ROOT,
    )

    res = run_cli(["status", "--state", str(state_file)], cwd=PROJECT_ROOT)
    assert res.returncode == 0
    assert "used_ok" in res.stdout
    assert "2" in res.stdout


def test_run_is_idempotent_skips_already_done(tmp_path):
    tokens_file = write_tokens(tmp_path / "tokens.csv", ["ak-1"])
    state_file = tmp_path / "state.json"
    log_dir = tmp_path / "logs"
    base_args = [
        "run",
        "--tokens", str(tokens_file),
        "--config", "c.py",
        "--state", str(state_file),
        "--logs", str(log_dir),
        "--cmd", sys.executable,
        "--cmd", str(FAKE_RUNNER),
        "--cmd", "{config}",
    ]
    res1 = run_cli(base_args, cwd=PROJECT_ROOT)
    res2 = run_cli(base_args, cwd=PROJECT_ROOT)
    assert res1.returncode == 0
    assert res2.returncode == 0
    log_files = list(log_dir.glob("*.log"))
    assert len(log_files) == 1


def test_run_errors_clearly_when_tokens_file_missing(tmp_path):
    state_file = tmp_path / "state.json"
    log_dir = tmp_path / "logs"
    res = run_cli(
        [
            "run",
            "--tokens", str(tmp_path / "missing.csv"),
            "--config", "c.py",
            "--state", str(state_file),
            "--logs", str(log_dir),
        ],
        cwd=PROJECT_ROOT,
    )
    assert res.returncode != 0
    combined = res.stderr + res.stdout
    assert "missing.csv" in combined or "not found" in combined.lower() or "no such" in combined.lower()
