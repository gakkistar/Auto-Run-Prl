import asyncio
import sys
from pathlib import Path

import pytest

from modal_orchestrator.runner import RunResult, run_one
from modal_orchestrator.tokens import TokenPair

from tests.conftest import FAKE_RUNNER


def fake_cmd_template():
    return [sys.executable, str(FAKE_RUNNER), "{config}"]


@pytest.mark.asyncio
async def test_returns_exit_code_zero_on_success(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    result = await run_one(
        token=TokenPair("ak-1", "as-1"),
        config_path="dummy-config.py",
        cmd_template=fake_cmd_template(),
        log_path=log_dir / "ak-1.log",
        env_overrides={"FAKE_EXIT_CODE": "0"},
    )
    assert isinstance(result, RunResult)
    assert result.exit_code == 0
    assert result.token.token_id == "ak-1"


@pytest.mark.asyncio
async def test_returns_nonzero_exit_code_on_failure(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    result = await run_one(
        token=TokenPair("ak-1", "as-1"),
        config_path="dummy-config.py",
        cmd_template=fake_cmd_template(),
        log_path=log_dir / "ak-1.log",
        env_overrides={"FAKE_EXIT_CODE": "7"},
    )
    assert result.exit_code == 7


@pytest.mark.asyncio
async def test_log_file_captures_stdout_and_stderr(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_path = log_dir / "ak-1.log"
    await run_one(
        token=TokenPair("ak-1", "as-1"),
        config_path="myapp.py",
        cmd_template=fake_cmd_template(),
        log_path=log_path,
        env_overrides={"FAKE_STDERR": "the-stderr-payload"},
    )
    content = log_path.read_text(encoding="utf-8")
    assert "MODAL_TOKEN_ID=ak-1" in content
    assert "MODAL_TOKEN_SECRET=as-1" in content
    assert "myapp.py" in content
    assert "the-stderr-payload" in content


@pytest.mark.asyncio
async def test_env_does_not_leak_overrides_into_parent_process(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.delenv("MODAL_TOKEN_ID", raising=False)
    await run_one(
        token=TokenPair("ak-x", "as-x"),
        config_path="c.py",
        cmd_template=fake_cmd_template(),
        log_path=log_dir / "x.log",
    )
    import os
    assert os.environ.get("MODAL_TOKEN_ID") is None


@pytest.mark.asyncio
async def test_can_be_cancelled_via_asyncio_task(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    task = asyncio.create_task(
        run_one(
            token=TokenPair("ak-1", "as-1"),
            config_path="c.py",
            cmd_template=fake_cmd_template(),
            log_path=log_dir / "ak-1.log",
            env_overrides={"FAKE_SLEEP": "30"},
        )
    )
    await asyncio.sleep(0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_config_placeholder_is_substituted(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_path = log_dir / "ak-1.log"
    await run_one(
        token=TokenPair("ak-1", "as-1"),
        config_path="path/to/my-config.py",
        cmd_template=fake_cmd_template(),
        log_path=log_path,
    )
    content = log_path.read_text(encoding="utf-8")
    assert "path/to/my-config.py" in content or r"path\to\my-config.py" in content
