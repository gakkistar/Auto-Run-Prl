"""Launch one Modal subprocess per token, with isolated env vars."""
from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .tokens import TokenPair


@dataclass
class RunResult:
    token: TokenPair
    exit_code: int
    log_path: Path


def _expand(cmd_template: Sequence[str], config_path: str) -> list[str]:
    return [arg.replace("{config}", config_path) for arg in cmd_template]


async def run_one(
    token: TokenPair,
    config_path: str,
    cmd_template: Sequence[str],
    log_path: Path,
    env_overrides: dict[str, str] | None = None,
) -> RunResult:
    """Run one Modal command for one token. Writes stdout+stderr to log_path.

    The parent process env is inherited but MODAL_TOKEN_ID / MODAL_TOKEN_SECRET
    are overridden with this token's credentials so the child sees only this
    workspace. env_overrides (used by tests) wins last.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["MODAL_TOKEN_ID"] = token.token_id
    env["MODAL_TOKEN_SECRET"] = token.token_secret
    if env_overrides:
        env.update(env_overrides)

    args = _expand(cmd_template, config_path)

    with log_path.open("w", encoding="utf-8") as logf:
        # Header makes logs self-describing even when read in isolation.
        logf.write(f"# token_id={token.token_id}\n# cmd={args!r}\n")
        logf.flush()

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=logf,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )

        try:
            exit_code = await proc.wait()
        except asyncio.CancelledError:
            # On cancel: try graceful terminate first, then kill.
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=15)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
            raise

    return RunResult(token=token, exit_code=exit_code, log_path=log_path)
