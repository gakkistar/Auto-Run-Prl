"""Async scheduler: claim tokens, dispatch via runner, record outcomes."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .runner import RunResult, run_one
from .state import StateStore, Status
from .tokens import TokenPair

log = logging.getLogger(__name__)


@dataclass
class SchedulerConfig:
    config_path: str
    cmd_template: Sequence[str]
    log_dir: Path
    max_parallel: int = 20
    shutdown_grace_seconds: float = 60.0
    per_token_env: Callable[[TokenPair], dict[str, str]] | None = None


def _safe_log_name(token_id: str) -> str:
    """Filename-safe rendering of a token id."""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in token_id)
    return safe[:64] or "token"


class Scheduler:
    def __init__(
        self,
        store: StateStore,
        tokens: Sequence[TokenPair],
        config: SchedulerConfig,
    ) -> None:
        self.store = store
        self.config = config
        self._tokens_by_id = {t.token_id: t for t in tokens}
        self._sem = asyncio.Semaphore(config.max_parallel)
        self._shutdown = asyncio.Event()
        self._in_flight: dict[str, asyncio.Task] = {}

    def request_shutdown(self) -> None:
        self._shutdown.set()

    async def _work_one(self, token: TokenPair) -> None:
        log_path = self.config.log_dir / f"{_safe_log_name(token.token_id)}.log"
        self.store.claim(token.token_id, log_path=str(log_path))

        env_overrides = None
        if self.config.per_token_env is not None:
            env_overrides = self.config.per_token_env(token)

        try:
            result: RunResult = await run_one(
                token=token,
                config_path=self.config.config_path,
                cmd_template=self.config.cmd_template,
                log_path=log_path,
                env_overrides=env_overrides,
            )
        except asyncio.CancelledError:
            self.store.mark_aborted(token.token_id)
            raise
        except Exception:
            log.exception("runner crashed for %s", token.token_id)
            self.store.mark_failed(token.token_id, exit_code=-1)
            return

        if result.exit_code == 0:
            self.store.mark_ok(token.token_id, exit_code=0)
        else:
            self.store.mark_failed(token.token_id, exit_code=result.exit_code)

    async def _worker(self, token: TokenPair) -> None:
        async with self._sem:
            if self._shutdown.is_set():
                self.store.mark_aborted(token.token_id)
                return
            await self._work_one(token)

    async def run(self) -> None:
        self.config.log_dir.mkdir(parents=True, exist_ok=True)

        claimable = list(self.store.claimable_ids())
        for tid in claimable:
            token = self._tokens_by_id.get(tid)
            if token is None:
                continue
            task = asyncio.create_task(self._worker(token), name=f"worker-{tid}")
            self._in_flight[tid] = task

        if not self._in_flight:
            return

        shutdown_task = asyncio.create_task(self._shutdown.wait(), name="shutdown")
        worker_tasks = list(self._in_flight.values())

        try:
            await asyncio.wait(
                worker_tasks + [shutdown_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            if self._shutdown.is_set():
                await self._graceful_shutdown(worker_tasks)
            else:
                pending_workers = [t for t in worker_tasks if not t.done()]
                if pending_workers:
                    await asyncio.gather(*pending_workers, return_exceptions=True)
        finally:
            shutdown_task.cancel()
            try:
                await shutdown_task
            except (asyncio.CancelledError, Exception):
                pass

    async def _graceful_shutdown(self, worker_tasks: list[asyncio.Task]) -> None:
        pending_workers = [t for t in worker_tasks if not t.done()]
        if not pending_workers:
            return
        log.info(
            "shutdown requested; waiting up to %.1fs for %d in-flight workers",
            self.config.shutdown_grace_seconds,
            len(pending_workers),
        )
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending_workers, return_exceptions=True),
                timeout=self.config.shutdown_grace_seconds,
            )
        except asyncio.TimeoutError:
            log.warning("grace period elapsed; cancelling remaining workers")
            for t in pending_workers:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*pending_workers, return_exceptions=True)
