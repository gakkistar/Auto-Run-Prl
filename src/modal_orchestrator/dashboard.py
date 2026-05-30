"""Read-only aiohttp web dashboard for live orchestrator progress."""
from __future__ import annotations

import asyncio
import logging
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_LOG_TAIL_LINES = 200

_INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>modal-orchestrator dashboard</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #1a1a2e; color: #e0e0e0; font-family: monospace; font-size: 14px; }
  #topbar { background: #16213e; padding: 10px 16px; display: flex; gap: 24px; align-items: center; border-bottom: 1px solid #0f3460; flex-wrap: wrap; }
  #topbar .stat { color: #a8d8ea; }
  #topbar .stat span { color: #f5f5f5; font-weight: bold; }
  #meta { margin-left: auto; font-size: 12px; color: #888; }
  #filters { padding: 8px 16px; background: #16213e; border-bottom: 1px solid #0f3460; display: flex; gap: 8px; flex-wrap: wrap; }
  .filter-btn { background: #0f3460; color: #a8d8ea; border: 1px solid #0f3460; padding: 4px 10px; cursor: pointer; border-radius: 3px; font-size: 12px; }
  .filter-btn.active { background: #e94560; color: #fff; border-color: #e94560; }
  #table-wrap { overflow-x: auto; padding: 16px; }
  table { width: 100%; border-collapse: collapse; }
  th { background: #0f3460; color: #a8d8ea; padding: 8px 10px; text-align: left; border-bottom: 2px solid #e94560; white-space: nowrap; }
  td { padding: 6px 10px; border-bottom: 1px solid #222; vertical-align: top; }
  tr:hover td { background: #1e2a40; }
  .status-available  { color: #a8d8ea; }
  .status-in_flight  { color: #f6c90e; }
  .status-used_ok    { color: #4caf50; }
  .status-used_failed { color: #e94560; }
  .status-used_aborted { color: #ff9800; }
  .log-btn { background: #0f3460; color: #a8d8ea; border: 1px solid #0f3460; padding: 2px 8px; cursor: pointer; border-radius: 3px; font-size: 11px; }
  .log-btn:hover { background: #e94560; border-color: #e94560; color: #fff; }
  #log-panel { margin: 0 16px 16px; background: #0d0d1a; border: 1px solid #0f3460; border-radius: 4px; display: none; }
  #log-panel-header { padding: 6px 12px; background: #0f3460; color: #a8d8ea; display: flex; justify-content: space-between; align-items: center; font-size: 12px; }
  #log-close { cursor: pointer; color: #e94560; font-size: 16px; line-height: 1; }
  #log-content { padding: 10px 12px; overflow-x: auto; max-height: 300px; overflow-y: auto; }
  #log-content pre { color: #c8c8c8; font-size: 12px; white-space: pre-wrap; word-break: break-all; }
  .hidden { display: none !important; }
</style>
</head>
<body>
<div id="topbar">
  <div class="stat">Total: <span id="s-total">-</span></div>
  <div class="stat">available: <span id="s-available">-</span></div>
  <div class="stat">in_flight: <span id="s-in_flight">-</span></div>
  <div class="stat">used_ok: <span id="s-used_ok">-</span></div>
  <div class="stat">used_failed: <span id="s-used_failed">-</span></div>
  <div class="stat">used_aborted: <span id="s-used_aborted">-</span></div>
  <div id="meta"><span id="last-updated">-</span> &nbsp;|&nbsp; Polling: 2s</div>
</div>
<div id="filters">
  <button class="filter-btn active" data-status="all">All</button>
  <button class="filter-btn" data-status="available">available</button>
  <button class="filter-btn" data-status="in_flight">in_flight</button>
  <button class="filter-btn" data-status="used_ok">used_ok</button>
  <button class="filter-btn" data-status="used_failed">used_failed</button>
  <button class="filter-btn" data-status="used_aborted">used_aborted</button>
</div>
<div id="table-wrap">
  <table>
    <thead><tr>
      <th>Token</th><th>Status</th><th>Claimed at</th><th>Finished at</th><th>Exit code</th><th>Log</th>
    </tr></thead>
    <tbody id="tbody"></tbody>
  </table>
</div>
<div id="log-panel">
  <div id="log-panel-header">
    <span id="log-panel-title">Log</span>
    <span id="log-close" title="Close">&#x2715;</span>
  </div>
  <div id="log-content"><pre id="log-pre"></pre></div>
</div>
<script>
var currentFilter = 'all';
var allTokens = [];

function setFilter(status) {
  currentFilter = status;
  document.querySelectorAll('.filter-btn').forEach(function(b) {
    b.classList.toggle('active', b.dataset.status === status);
  });
  renderTable();
}

function renderTable() {
  var tbody = document.getElementById('tbody');
  var rows = '';
  var filtered = currentFilter === 'all' ? allTokens : allTokens.filter(function(t) { return t.status === currentFilter; });
  filtered.forEach(function(t) {
    var cls = 'status-' + t.status;
    var logBtn = '<button class="log-btn" onclick="fetchLog(' + JSON.stringify(t.token_id) + ')">view</button>';
    rows += '<tr>'
      + '<td>' + esc(t.token_id) + '</td>'
      + '<td class="' + cls + '">' + esc(t.status) + '</td>'
      + '<td>' + (t.claimed_at ? esc(t.claimed_at) : '-') + '</td>'
      + '<td>' + (t.finished_at ? esc(t.finished_at) : '-') + '</td>'
      + '<td>' + (t.exit_code !== null && t.exit_code !== undefined ? t.exit_code : '-') + '</td>'
      + '<td>' + logBtn + '</td>'
      + '</tr>';
  });
  tbody.innerHTML = rows || '<tr><td colspan="6" style="text-align:center;color:#888;">No tokens match.</td></tr>';
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function fetchLog(tokenId) {
  var panel = document.getElementById('log-panel');
  var pre = document.getElementById('log-pre');
  var title = document.getElementById('log-panel-title');
  title.textContent = 'Log: ' + tokenId;
  pre.textContent = 'Loading...';
  panel.style.display = 'block';
  fetch('/api/log/' + encodeURIComponent(tokenId))
    .then(function(r) { return r.text(); })
    .then(function(t) { pre.textContent = t; })
    .catch(function(e) { pre.textContent = 'Error: ' + e; });
}

document.getElementById('log-close').onclick = function() {
  document.getElementById('log-panel').style.display = 'none';
};

document.querySelectorAll('.filter-btn').forEach(function(b) {
  b.addEventListener('click', function() { setFilter(b.dataset.status); });
});

function poll() {
  fetch('/api/state')
    .then(function(r) { return r.json(); })
    .then(function(data) {
      var c = data.counts || {};
      document.getElementById('s-total').textContent = data.total || 0;
      document.getElementById('s-available').textContent = c.available || 0;
      document.getElementById('s-in_flight').textContent = c.in_flight || 0;
      document.getElementById('s-used_ok').textContent = c.used_ok || 0;
      document.getElementById('s-used_failed').textContent = c.used_failed || 0;
      document.getElementById('s-used_aborted').textContent = c.used_aborted || 0;
      allTokens = data.tokens || [];
      renderTable();
      document.getElementById('last-updated').textContent = 'Last updated: ' + new Date().toLocaleTimeString();
    })
    .catch(function() {
      document.getElementById('last-updated').textContent = 'Last updated: error';
    });
}

poll();
setInterval(poll, 2000);
</script>
</body>
</html>
"""


def _build_app(store, log_dir: Path):
    """Build and return the aiohttp Application."""
    from aiohttp import web

    routes = web.RouteTableDef()

    @routes.get("/")
    async def index(request: web.Request) -> web.Response:
        return web.Response(
            text=_INDEX_HTML,
            content_type="text/html",
            charset="utf-8",
        )

    @routes.get("/api/state")
    async def api_state(request: web.Request) -> web.Response:
        records = store.records()
        counts = {s: 0 for s in ("available", "in_flight", "used_ok", "used_failed", "used_aborted")}
        tokens_list = []
        for token_id, rec in records.items():
            status_val = rec.status.value
            if status_val in counts:
                counts[status_val] += 1
            tokens_list.append({
                "token_id": token_id,
                "status": status_val,
                "claimed_at": rec.claimed_at,
                "finished_at": rec.finished_at,
                "exit_code": rec.exit_code,
                "log_path": rec.log_path,
            })
        payload = {
            "counts": counts,
            "total": len(records),
            "tokens": tokens_list,
            "as_of": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        }
        return web.Response(
            text=json.dumps(payload),
            content_type="application/json",
            charset="utf-8",
        )

    @routes.get("/api/log/{token_id}")
    async def api_log(request: web.Request) -> web.Response:
        token_id = request.match_info["token_id"]
        records = store.records()
        if token_id not in records:
            return web.Response(
                text="unknown token",
                status=404,
                content_type="text/plain",
                charset="utf-8",
            )
        rec = records[token_id]
        if not rec.log_path:
            return web.Response(
                text="(no log yet)",
                content_type="text/plain",
                charset="utf-8",
            )
        log_file = Path(rec.log_path)
        if not log_file.exists():
            return web.Response(
                text="(no log yet)",
                content_type="text/plain",
                charset="utf-8",
            )
        try:
            text = await asyncio.to_thread(
                log_file.read_text, encoding="utf-8", errors="replace"
            )
            lines = text.splitlines()
            tail = "\n".join(lines[-_LOG_TAIL_LINES:])
        except OSError as exc:
            tail = f"(error reading log: {exc})"
        return web.Response(
            text=tail,
            content_type="text/plain",
            charset="utf-8",
        )

    app = web.Application()
    app.add_routes(routes)
    return app


async def start_dashboard(
    store,
    host: str,
    port: int,
    log_dir: Path,
) -> Optional["aiohttp.web.AppRunner"]:
    """Start an aiohttp dashboard. Returns the AppRunner so caller can clean up.

    Returns None if aiohttp can't bind (port in use, etc.).
    """
    if host not in ("127.0.0.1", "::1", "localhost"):
        logger.warning(
            "Dashboard bound to %s — visible on all interfaces. No auth.", host
        )

    from aiohttp import web

    app = _build_app(store, log_dir)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    try:
        await site.start()
    except OSError as exc:
        logger.warning(
            "Dashboard could not bind to %s:%d — %s. Continuing without dashboard.",
            host,
            port,
            exc,
        )
        await runner.cleanup()
        return None
    return runner


async def stop_dashboard(runner) -> None:
    """Cleanly stop the dashboard."""
    try:
        await runner.cleanup()
    except Exception as exc:
        logger.warning("Error stopping dashboard: %s", exc)
