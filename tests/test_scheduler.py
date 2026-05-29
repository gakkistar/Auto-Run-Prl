import asyncio
import sys
import time
from pathlib import Path

import pytest

from modal_orchestrator.scheduler import Scheduler, SchedulerConfig
from modal_orchestrator.state import StateStore, Status
from modal_orchestrator.tokens import TokenPair

from tests.conftest import FAKE_RUNNER


def fake_cmd_template():
    return [sys.executable, str(FAKE_RUNNER), "{config}"]


def build_store(tmp_path, ids):
    store = StateStore(tmp_path / "state.json")
    store.ensure_available(ids)
    return store


def token_pool(ids):
    return [TokenPair(tid, f"secret-{tid}") for tid in ids]


@pytest.mark.asyncio
async def test_runs_every_available_token_to_completion(tmp_path):
    store = build_store(tmp_path, ["ak-1", "ak-2", "ak-3"])
    pool = token_pool(["ak-1", "ak-2", "ak-3"])
    sched = Scheduler(
        store=store,
        tokens=pool,
        config=SchedulerConfig(
            config_path="dummy.py",
            cmd_template=fake_cmd_template(),
            log_dir=tmp_path / "logs",
            max_parallel=2,
        ),
    )
    await sched.run()
    assert store.all_terminal()
    assert all(r.status == Status.USED_OK for r in store.records().values())


@pytest.mark.asyncio
async def test_failures_do_not_stop_other_workers(tmp_path, monkeypatch):
    store = build_store(tmp_path, ["ak-fail-1", "ak-ok", "ak-fail-2"])
    pool = token_pool(["ak-fail-1", "ak-ok", "ak-fail-2"])

    sched = Scheduler(
        store=store,
        tokens=pool,
        config=SchedulerConfig(
            config_path="dummy.py",
            cmd_template=fake_cmd_template(),
            log_dir=tmp_path / "logs",
            max_parallel=3,
            per_token_env=lambda t: {
                "FAKE_EXIT_CODE": "5" if "fail" in t.token_id else "0"
            },
        ),
    )
    await sched.run()
    recs = store.records()
    assert recs["ak-ok"].status == Status.USED_OK
    assert recs["ak-fail-1"].status == Status.USED_FAILED
    assert recs["ak-fail-2"].status == Status.USED_FAILED


@pytest.mark.asyncio
async def test_semaphore_caps_concurrency(tmp_path):
    """With 4 tokens that each sleep 1s and max_parallel=2, total wall time
    should be ~2s (two batches of 2), not ~1s (all at once) or ~4s (serial)."""
    store = build_store(tmp_path, ["a", "b", "c", "d"])
    pool = token_pool(["a", "b", "c", "d"])
    sched = Scheduler(
        store=store,
        tokens=pool,
        config=SchedulerConfig(
            config_path="dummy.py",
            cmd_template=fake_cmd_template(),
            log_dir=tmp_path / "logs",
            max_parallel=2,
            per_token_env=lambda t: {"FAKE_SLEEP": "1"},
        ),
    )
    start = time.monotonic()
    await sched.run()
    elapsed = time.monotonic() - start
    assert 1.5 < elapsed < 3.5, f"elapsed={elapsed}"


@pytest.mark.asyncio
async def test_skips_already_terminal_tokens(tmp_path):
    store = build_store(tmp_path, ["ak-1", "ak-2"])
    store.claim("ak-1", log_path="logs/ak-1.log")
    store.mark_ok("ak-1", exit_code=0)
    pool = token_pool(["ak-1", "ak-2"])
    sched = Scheduler(
        store=store,
        tokens=pool,
        config=SchedulerConfig(
            config_path="dummy.py",
            cmd_template=fake_cmd_template(),
            log_dir=tmp_path / "logs",
            max_parallel=2,
        ),
    )
    await sched.run()
    assert store.records()["ak-1"].status == Status.USED_OK
    assert store.records()["ak-2"].status == Status.USED_OK


@pytest.mark.asyncio
async def test_request_shutdown_stops_dispatch_and_marks_in_flight_aborted(tmp_path):
    store = build_store(tmp_path, ["a", "b", "c"])
    pool = token_pool(["a", "b", "c"])
    sched = Scheduler(
        store=store,
        tokens=pool,
        config=SchedulerConfig(
            config_path="dummy.py",
            cmd_template=fake_cmd_template(),
            log_dir=tmp_path / "logs",
            max_parallel=3,
            per_token_env=lambda t: {"FAKE_SLEEP": "10"},
            shutdown_grace_seconds=1,
        ),
    )

    async def trigger_shutdown_after_delay():
        await asyncio.sleep(0.5)
        sched.request_shutdown()

    await asyncio.gather(sched.run(), trigger_shutdown_after_delay())
    recs = store.records()
    assert all(
        r.status == Status.USED_ABORTED for r in recs.values()
    ), f"expected all aborted, got {[(tid, r.status.value) for tid, r in recs.items()]}"
    assert store.all_terminal()


@pytest.mark.asyncio
async def test_shutdown_after_first_worker_completes_still_aborts_remaining(tmp_path):
    """Regression: shutdown signal raised AFTER the first worker completes
    must still trigger graceful shutdown. A single asyncio.wait+branch lets
    the shutdown signal slip through if a worker happens to finish first."""
    store = build_store(tmp_path, ["fast", "slow-1", "slow-2"])
    pool = token_pool(["fast", "slow-1", "slow-2"])
    sched = Scheduler(
        store=store,
        tokens=pool,
        config=SchedulerConfig(
            config_path="dummy.py",
            cmd_template=fake_cmd_template(),
            log_dir=tmp_path / "logs",
            max_parallel=3,
            per_token_env=lambda t: (
                {"FAKE_SLEEP": "0"} if t.token_id == "fast"
                else {"FAKE_SLEEP": "20"}
            ),
            shutdown_grace_seconds=1,
        ),
    )

    async def trigger_after_fast_done():
        # Give the fast worker a comfortable margin to finish, then signal
        # shutdown. The two slow workers should still be running.
        await asyncio.sleep(1.5)
        sched.request_shutdown()

    await asyncio.gather(sched.run(), trigger_after_fast_done())
    recs = store.records()
    assert recs["fast"].status == Status.USED_OK
    assert recs["slow-1"].status == Status.USED_ABORTED, recs["slow-1"].status
    assert recs["slow-2"].status == Status.USED_ABORTED, recs["slow-2"].status
