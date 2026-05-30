"""Tests for the aiohttp dashboard."""
from __future__ import annotations

import json
import socket
import tempfile
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from modal_orchestrator.dashboard import _build_app, start_dashboard
from modal_orchestrator.state import StateStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(tmp_path: Path) -> StateStore:
    """Return a fresh in-memory StateStore backed by a temp file."""
    return StateStore(tmp_path / "state.json")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_state_endpoint_returns_counts_and_tokens(tmp_path):
    store = _make_store(tmp_path)
    store.ensure_available(["ak-1", "ak-2"])
    store.claim("ak-1", log_path=str(tmp_path / "ak-1.log"))
    store.mark_ok("ak-1", exit_code=0)

    app = _build_app(store, tmp_path)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/state")
        assert resp.status == 200
        data = await resp.json()

    counts = data["counts"]
    assert counts["used_ok"] == 1
    assert counts["available"] == 1
    assert data["total"] == 2
    token_ids = {t["token_id"] for t in data["tokens"]}
    assert "ak-1" in token_ids
    assert "ak-2" in token_ids
    # Find ak-1 record and verify status
    ak1 = next(t for t in data["tokens"] if t["token_id"] == "ak-1")
    assert ak1["status"] == "used_ok"
    assert ak1["exit_code"] == 0


@pytest.mark.asyncio
async def test_log_endpoint_returns_tail_of_log_file(tmp_path):
    store = _make_store(tmp_path)
    store.ensure_available(["ak-1"])
    log_file = tmp_path / "ak-1.log"
    # Write 250 lines so we can check the tail behaviour
    lines = [f"line {i}" for i in range(250)]
    log_file.write_text("\n".join(lines), encoding="utf-8")
    store.claim("ak-1", log_path=str(log_file))

    app = _build_app(store, tmp_path)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/log/ak-1")
        assert resp.status == 200
        body = await resp.text()

    # Last 200 lines are lines 50-249
    assert "line 249" in body
    assert "line 50" in body
    # Lines before the tail should not appear
    assert "line 49" not in body


@pytest.mark.asyncio
async def test_log_endpoint_returns_no_log_yet_when_file_missing(tmp_path):
    store = _make_store(tmp_path)
    store.ensure_available(["ak-1"])
    # Claim with a path that doesn't exist on disk
    store.claim("ak-1", log_path=str(tmp_path / "ak-1.log"))

    app = _build_app(store, tmp_path)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/log/ak-1")
        assert resp.status == 200
        body = await resp.text()

    assert "(no log yet)" in body


@pytest.mark.asyncio
async def test_log_endpoint_returns_404_for_unknown_token(tmp_path):
    store = _make_store(tmp_path)
    store.ensure_available(["ak-1"])

    app = _build_app(store, tmp_path)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/api/log/ak-unknown")
        assert resp.status == 404


@pytest.mark.asyncio
async def test_index_serves_html(tmp_path):
    store = _make_store(tmp_path)

    app = _build_app(store, tmp_path)
    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/")
        assert resp.status == 200
        assert "text/html" in resp.content_type
        body = await resp.text()

    assert "Total:" in body
    assert "used_ok" in body


@pytest.mark.asyncio
async def test_start_dashboard_returns_none_when_port_busy(tmp_path):
    store = _make_store(tmp_path)

    # Find a free port, then hold it so start_dashboard can't bind.
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        busy_port = blocker.getsockname()[1]

        runner = await start_dashboard(
            store=store,
            host="127.0.0.1",
            port=busy_port,
            log_dir=tmp_path,
        )
        assert runner is None
    finally:
        blocker.close()
