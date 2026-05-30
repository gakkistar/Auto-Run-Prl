# Modal Token Orchestrator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local Python orchestrator that runs the user's Modal GPU workload across hundreds-to-thousands of Modal workspace tokens — launching as many parallel H100 containers as bandwidth allows, persisting per-token state for clean resume, and terminating when every token in the pool has been used once.

**Architecture:** Single-purpose Python CLI app (no web service, no daemon).
- Token pool loaded from a CSV file (`token_id,token_secret` per line).
- Per-token JSON state file (`state.json`) persisted after every status transition, enabling resume.
- An asyncio-based worker pool launches each token as an isolated subprocess: `modal run <config-file>` with `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` injected via `env=`. No mutation of `~/.modal.toml`, no per-thread global state, no need for the modal Python SDK at the orchestrator level.
- Fixed max parallelism (CLI flag, default 20). Subprocess output streamed to per-token log files.
- On Ctrl+C: stop dispatching new work, wait for in-flight subprocesses (configurable grace, default 60s), then terminate. State is persisted at every transition so even SIGKILL leaves resumable state.
- On per-token failure (any non-zero exit, including quota exhaustion): mark `used_failed`, never retry. User confirmed "认赔、丢掉这次运行".
- Terminates when every token in the pool has reached a terminal status (`used_ok` / `used_failed` / `used_aborted`).

**Tech Stack:** Python 3.10+ (asyncio, stdlib argparse, pathlib, dataclasses), `modal` CLI installed via pip and invoked as a subprocess, `pytest` + `pytest-asyncio` for tests. No third-party runtime dependencies beyond `modal` itself.

**Design notes (captured here because the formal spec phase was skipped):**
- The orchestrator is **workload-agnostic**. It does not parse the config file; it shells out to `modal run <config>`. The config file is the user's existing Modal app — it defines its own `@app.function(gpu="H100")` and its own output handling. If the user later needs a non-default launch command (e.g., `python <config>` or `modal run <config>::entrypoint`), they can pass `--cmd-template`.
- Each card runs the **same config with no shard parameter** — pure replicas. Confirmed with user.
- **Outputs** are handled inside the config (e.g., S3 upload, HF push). The orchestrator does not collect or post-process outputs.
- **Why subprocesses, not the Python SDK?** Modal's Python client caches authentication state at module-import time. Setting `MODAL_TOKEN_ID` per-thread inside one process is not safe with hundreds of concurrent workspaces. Subprocesses give clean per-token isolation.
- **No retry on token exhaustion.** Confirmed with user. Failed tokens get logged and the scheduler moves on.

---

## File Structure

```
E:\code\auto-run-prl\
├── .gitignore
├── pyproject.toml
├── README.md
├── tokens.example.csv               # Sample token-pool file (committed, not real tokens)
├── src/
│   └── modal_orchestrator/
│       ├── __init__.py
│       ├── __main__.py              # `python -m modal_orchestrator` entry
│       ├── cli.py                   # argparse + main()
│       ├── tokens.py                # TokenPair dataclass + CSV loader
│       ├── state.py                 # JSON state file (atomic write, status transitions)
│       ├── runner.py                # Subprocess launcher (one token → one container)
│       └── scheduler.py             # Async pool, semaphore, signal handling, main loop
└── tests/
    ├── __init__.py
    ├── conftest.py                  # Shared fixtures (paths to fake_runner, etc.)
    ├── fixtures/
    │   └── fake_runner.py           # Stand-in for `modal run` in tests
    ├── test_tokens.py
    ├── test_state.py
    ├── test_runner.py
    ├── test_scheduler.py
    └── test_cli.py
```

**Module boundaries (one responsibility each):**
- `tokens.py` only parses token files. No I/O for state.
- `state.py` only persists and queries token statuses. No subprocesses.
- `runner.py` only launches one subprocess with env vars and reports its exit code. No pool logic.
- `scheduler.py` owns concurrency: pulls a token from state, calls runner, records outcome. No CLI parsing.
- `cli.py` only parses args and wires everything together.

---

## Task 0: Bootstrap project

**Files:**
- Create: `E:\code\auto-run-prl\.gitignore`
- Create: `E:\code\auto-run-prl\pyproject.toml`
- Create: `E:\code\auto-run-prl\src\modal_orchestrator\__init__.py`
- Create: `E:\code\auto-run-prl\tests\__init__.py`
- Create: `E:\code\auto-run-prl\tests\conftest.py`
- Create: `E:\code\auto-run-prl\tests\fixtures\fake_runner.py`

- [ ] **Step 1: Initialize git repository**

Run (PowerShell):

```powershell
git init E:\code\auto-run-prl
```

Expected output: `Initialized empty Git repository in E:/code/auto-run-prl/.git/`

If git user.name / user.email aren't set globally, configure them now:

```powershell
git -C E:\code\auto-run-prl config user.email "you@example.com"
git -C E:\code\auto-run-prl config user.name "Your Name"
```

- [ ] **Step 2: Create `.gitignore`**

File: `E:\code\auto-run-prl\.gitignore`

```
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/
dist/
build/
.pytest_cache/

# Project artifacts (must not commit secrets)
state.json
state.json.tmp
logs/
tokens.csv
*.log

# IDE
.vscode/
.idea/
```

- [ ] **Step 3: Create `pyproject.toml`**

File: `E:\code\auto-run-prl\pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "modal-orchestrator"
version = "0.1.0"
description = "Run a Modal workload across many workspace tokens in parallel."
requires-python = ">=3.10"
dependencies = [
    "modal>=0.62",
]

[project.optional-dependencies]
dev = [
    "pytest>=7",
    "pytest-asyncio>=0.21",
]

[project.scripts]
modal-orchestrator = "modal_orchestrator.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 4: Create package and test skeleton**

File: `E:\code\auto-run-prl\src\modal_orchestrator\__init__.py`

```python
__version__ = "0.1.0"
```

File: `E:\code\auto-run-prl\tests\__init__.py`

Leave empty (empty file marks the directory as a package).

File: `E:\code\auto-run-prl\tests\conftest.py`

```python
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FAKE_RUNNER = FIXTURES_DIR / "fake_runner.py"
```

- [ ] **Step 5: Create `fake_runner.py` fixture**

File: `E:\code\auto-run-prl\tests\fixtures\fake_runner.py`

```python
"""Stand-in for `modal run <config>` used in tests.

Reads behavior from env vars so a single fixture can simulate
many outcomes:
  FAKE_EXIT_CODE  - exit with this code (default 0)
  FAKE_SLEEP      - seconds to sleep before exiting (default 0)
  FAKE_STDERR     - message to write to stderr (optional)

Always echoes the MODAL_TOKEN_ID and MODAL_TOKEN_SECRET it received
so tests can confirm env propagation.
"""
import os
import sys
import time

exit_code = int(os.environ.get("FAKE_EXIT_CODE", "0"))
sleep_seconds = float(os.environ.get("FAKE_SLEEP", "0"))
stderr_msg = os.environ.get("FAKE_STDERR")

print(f"fake_runner args={sys.argv[1:]}")
print(f"MODAL_TOKEN_ID={os.environ.get('MODAL_TOKEN_ID', '<unset>')}")
print(f"MODAL_TOKEN_SECRET={os.environ.get('MODAL_TOKEN_SECRET', '<unset>')}")

if stderr_msg:
    print(stderr_msg, file=sys.stderr)
if sleep_seconds > 0:
    time.sleep(sleep_seconds)

sys.exit(exit_code)
```

- [ ] **Step 6: Set up virtualenv and install dependencies**

Run (PowerShell):

```powershell
cd E:\code\auto-run-prl
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Expected: a final line like `Successfully installed modal-... pytest-... pytest-asyncio-... modal-orchestrator-0.1.0`.

If `Activate.ps1` is blocked by execution policy, run once per session:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

- [ ] **Step 7: Verify pytest discovers and reports zero tests**

Run: `pytest`

Expected: `no tests ran in 0.XXs` (no errors, just zero collected).

- [ ] **Step 8: Commit**

```powershell
git -C E:\code\auto-run-prl add .gitignore pyproject.toml src/ tests/
git -C E:\code\auto-run-prl commit -m "chore: bootstrap modal-orchestrator package skeleton"
```

Expected: a single commit listing the new files.

---

## Task 1: Token loader (`tokens.py`)

**Goal:** Parse a CSV file of `token_id,token_secret` pairs into a list of `TokenPair` dataclasses. Skip blank lines and `#` comments. Detect and skip an optional header row. De-duplicate by `token_id` (keep first occurrence). Raise on malformed lines.

**Files:**
- Create: `E:\code\auto-run-prl\src\modal_orchestrator\tokens.py`
- Create: `E:\code\auto-run-prl\tests\test_tokens.py`

- [ ] **Step 1: Write failing tests**

File: `E:\code\auto-run-prl\tests\test_tokens.py`

```python
import pytest
from pathlib import Path

from modal_orchestrator.tokens import TokenPair, load_tokens, MalformedTokenLine


def write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "tokens.csv"
    p.write_text(content, encoding="utf-8")
    return p


def test_parses_two_simple_lines(tmp_path):
    f = write(tmp_path, "ak-1,as-1\nak-2,as-2\n")
    assert load_tokens(f) == [
        TokenPair("ak-1", "as-1"),
        TokenPair("ak-2", "as-2"),
    ]


def test_skips_blank_lines_and_hash_comments(tmp_path):
    f = write(tmp_path, "# comment\n\nak-1,as-1\n\n# another\nak-2,as-2\n")
    assert load_tokens(f) == [
        TokenPair("ak-1", "as-1"),
        TokenPair("ak-2", "as-2"),
    ]


def test_skips_header_row_if_present(tmp_path):
    f = write(tmp_path, "token_id,token_secret\nak-1,as-1\n")
    assert load_tokens(f) == [TokenPair("ak-1", "as-1")]


def test_deduplicates_by_token_id_keeping_first(tmp_path):
    f = write(tmp_path, "ak-1,as-A\nak-2,as-2\nak-1,as-B\n")
    assert load_tokens(f) == [
        TokenPair("ak-1", "as-A"),
        TokenPair("ak-2", "as-2"),
    ]


def test_strips_whitespace_around_fields(tmp_path):
    f = write(tmp_path, "  ak-1 , as-1  \n")
    assert load_tokens(f) == [TokenPair("ak-1", "as-1")]


def test_raises_on_malformed_line(tmp_path):
    f = write(tmp_path, "ak-1,as-1\nbroken-line-no-comma\n")
    with pytest.raises(MalformedTokenLine) as exc:
        load_tokens(f)
    assert "line 2" in str(exc.value)


def test_raises_on_empty_field(tmp_path):
    f = write(tmp_path, "ak-1,\n")
    with pytest.raises(MalformedTokenLine):
        load_tokens(f)


def test_missing_file_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_tokens(tmp_path / "does-not-exist.csv")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_tokens.py -v`

Expected: All 8 tests fail with `ModuleNotFoundError: No module named 'modal_orchestrator.tokens'`.

- [ ] **Step 3: Write minimal implementation**

File: `E:\code\auto-run-prl\src\modal_orchestrator\tokens.py`

```python
"""Load Modal workspace tokens from a CSV file."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class TokenPair:
    """A Modal workspace authentication pair."""
    token_id: str
    token_secret: str


class MalformedTokenLine(ValueError):
    """Raised when a line in the token file cannot be parsed."""


def load_tokens(path: Path | str) -> list[TokenPair]:
    """Read a CSV of token_id,token_secret pairs.

    Skips blank lines and lines starting with '#'. If the first non-skipped
    line is exactly 'token_id,token_secret' it is treated as a header.
    Duplicate token_ids are dropped (first occurrence wins). Raises
    MalformedTokenLine on any line that doesn't have exactly two non-empty
    comma-separated fields.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)

    pairs: list[TokenPair] = []
    seen_ids: set[str] = set()

    with p.open("r", encoding="utf-8") as f:
        lines = f.readlines()

    header_consumed = False
    for line_no, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if not header_consumed and stripped.lower() == "token_id,token_secret":
            header_consumed = True
            continue
        header_consumed = True  # only the first eligible line can be a header

        parts = [p.strip() for p in stripped.split(",")]
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise MalformedTokenLine(
                f"line {line_no}: expected 'token_id,token_secret', got {raw!r}"
            )

        token_id, token_secret = parts
        if token_id in seen_ids:
            continue
        seen_ids.add(token_id)
        pairs.append(TokenPair(token_id, token_secret))

    return pairs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_tokens.py -v`

Expected: all 8 tests PASS.

- [ ] **Step 5: Commit**

```powershell
git -C E:\code\auto-run-prl add src/modal_orchestrator/tokens.py tests/test_tokens.py
git -C E:\code\auto-run-prl commit -m "feat(tokens): load + dedupe Modal workspace tokens from CSV"
```

---

## Task 2: State persistence (`state.py`)

**Goal:** A JSON state file that maps `token_id → status`. Atomic write via tmp + `os.replace`. Status transitions: `claim`, `mark_ok`, `mark_failed`, `mark_aborted`. Resume: on startup, load existing state, return only tokens that are still claimable (`available`, plus optionally `in_flight` → reset to `available` if `--retry-aborted`).

**Status values:**
- `available` — in pool, never touched.
- `in_flight` — currently being used by a running subprocess.
- `used_ok` — finished with exit code 0.
- `used_failed` — finished with non-zero exit code (any reason).
- `used_aborted` — subprocess was terminated (Ctrl+C, timeout, killed).

**State file shape:**
```json
{
  "version": 1,
  "tokens": {
    "ak-abc...": {
      "status": "used_ok",
      "claimed_at": "2026-05-29T12:00:00.000+00:00",
      "finished_at": "2026-05-29T12:42:13.000+00:00",
      "exit_code": 0,
      "log_path": "logs/ak-abc.log"
    }
  }
}
```

**Files:**
- Create: `E:\code\auto-run-prl\src\modal_orchestrator\state.py`
- Create: `E:\code\auto-run-prl\tests\test_state.py`

- [ ] **Step 1: Write failing tests**

File: `E:\code\auto-run-prl\tests\test_state.py`

```python
import json
from pathlib import Path

import pytest

from modal_orchestrator.state import (
    Status,
    StateStore,
    TokenRecord,
)


def test_creates_empty_store_when_file_missing(tmp_path):
    store = StateStore(tmp_path / "state.json")
    assert store.records() == {}


def test_round_trip_persists_to_disk(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.ensure_available(["ak-1", "ak-2"])

    reloaded = StateStore(path)
    assert set(reloaded.records().keys()) == {"ak-1", "ak-2"}
    assert reloaded.records()["ak-1"].status == Status.AVAILABLE


def test_ensure_available_is_idempotent_and_does_not_clobber(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.ensure_available(["ak-1"])
    store.claim("ak-1", log_path="logs/ak-1.log")
    # Re-running ensure_available must not overwrite the in_flight status.
    store.ensure_available(["ak-1", "ak-2"])
    recs = store.records()
    assert recs["ak-1"].status == Status.IN_FLIGHT
    assert recs["ak-2"].status == Status.AVAILABLE


def test_claim_and_mark_ok(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.ensure_available(["ak-1"])
    store.claim("ak-1", log_path="logs/ak-1.log")
    assert store.records()["ak-1"].status == Status.IN_FLIGHT
    store.mark_ok("ak-1", exit_code=0)
    rec = store.records()["ak-1"]
    assert rec.status == Status.USED_OK
    assert rec.exit_code == 0
    assert rec.finished_at is not None


def test_mark_failed(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.ensure_available(["ak-1"])
    store.claim("ak-1", log_path="logs/ak-1.log")
    store.mark_failed("ak-1", exit_code=2)
    rec = store.records()["ak-1"]
    assert rec.status == Status.USED_FAILED
    assert rec.exit_code == 2


def test_mark_aborted(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.ensure_available(["ak-1"])
    store.claim("ak-1", log_path="logs/ak-1.log")
    store.mark_aborted("ak-1")
    assert store.records()["ak-1"].status == Status.USED_ABORTED


def test_remaining_returns_only_claimable(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.ensure_available(["ak-1", "ak-2", "ak-3", "ak-4"])
    store.claim("ak-2", log_path="logs/ak-2.log")
    store.mark_ok("ak-2", exit_code=0)
    store.claim("ak-3", log_path="logs/ak-3.log")
    store.mark_failed("ak-3", exit_code=1)
    # ak-1 still available, ak-4 still available, ak-2 done, ak-3 done.
    assert set(store.claimable_ids()) == {"ak-1", "ak-4"}


def test_writes_valid_json_with_version_field(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.ensure_available(["ak-1"])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert "ak-1" in data["tokens"]


def test_resume_resets_in_flight_to_aborted_by_default(tmp_path):
    """On reload, in_flight tokens are assumed to have been interrupted by a
    crash and are marked used_aborted (not re-claimable). This protects us
    from double-spending credit on Modal-side containers that may still be
    running."""
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.ensure_available(["ak-1"])
    store.claim("ak-1", log_path="logs/ak-1.log")
    # Simulate crash: don't call mark_ok / mark_failed.
    reloaded = StateStore(path)
    assert reloaded.records()["ak-1"].status == Status.USED_ABORTED


def test_resume_with_retry_aborted_resets_in_flight_to_available(tmp_path):
    path = tmp_path / "state.json"
    store = StateStore(path)
    store.ensure_available(["ak-1"])
    store.claim("ak-1", log_path="logs/ak-1.log")
    reloaded = StateStore(path, retry_aborted=True)
    assert reloaded.records()["ak-1"].status == Status.AVAILABLE


def test_records_include_timestamps(tmp_path):
    store = StateStore(tmp_path / "state.json")
    store.ensure_available(["ak-1"])
    store.claim("ak-1", log_path="logs/ak-1.log")
    rec = store.records()["ak-1"]
    assert rec.claimed_at is not None
    assert rec.log_path == "logs/ak-1.log"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_state.py -v`

Expected: 11 errors, all `ModuleNotFoundError: No module named 'modal_orchestrator.state'`.

- [ ] **Step 3: Write minimal implementation**

File: `E:\code\auto-run-prl\src\modal_orchestrator\state.py`

```python
"""On-disk JSON state for the token pool.

Atomic writes via tmp+os.replace. On reload, any token left in IN_FLIGHT
is conservatively marked USED_ABORTED (we cannot tell whether the previous
process actually consumed credit, but we must assume it might have, and
re-using the same token risks double-billing the same workspace).
Pass retry_aborted=True to override: in_flight tokens are reset to available.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable


class Status(str, Enum):
    AVAILABLE = "available"
    IN_FLIGHT = "in_flight"
    USED_OK = "used_ok"
    USED_FAILED = "used_failed"
    USED_ABORTED = "used_aborted"


TERMINAL = {Status.USED_OK, Status.USED_FAILED, Status.USED_ABORTED}


@dataclass
class TokenRecord:
    token_id: str
    status: Status = Status.AVAILABLE
    claimed_at: str | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    log_path: str | None = None

    def to_json(self) -> dict:
        return {
            "status": self.status.value,
            "claimed_at": self.claimed_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "log_path": self.log_path,
        }

    @classmethod
    def from_json(cls, token_id: str, obj: dict) -> "TokenRecord":
        return cls(
            token_id=token_id,
            status=Status(obj.get("status", Status.AVAILABLE.value)),
            claimed_at=obj.get("claimed_at"),
            finished_at=obj.get("finished_at"),
            exit_code=obj.get("exit_code"),
            log_path=obj.get("log_path"),
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class StateStore:
    VERSION = 1

    def __init__(self, path: Path | str, retry_aborted: bool = False) -> None:
        self.path = Path(path)
        self._records: dict[str, TokenRecord] = {}
        self._load(retry_aborted=retry_aborted)

    def _load(self, retry_aborted: bool) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return
        for token_id, obj in data.get("tokens", {}).items():
            rec = TokenRecord.from_json(token_id, obj)
            if rec.status == Status.IN_FLIGHT:
                if retry_aborted:
                    rec.status = Status.AVAILABLE
                    rec.claimed_at = None
                else:
                    rec.status = Status.USED_ABORTED
                    rec.finished_at = _now_iso()
            self._records[token_id] = rec
        if (
            any(r.status == Status.USED_ABORTED for r in self._records.values())
            or retry_aborted
        ):
            self._flush()

    def _flush(self) -> None:
        payload = {
            "version": self.VERSION,
            "tokens": {tid: rec.to_json() for tid, rec in self._records.items()},
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=self.path.name + ".",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, self.path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def ensure_available(self, token_ids: Iterable[str]) -> None:
        """Insert tokens we've never seen. Existing records are not modified."""
        dirty = False
        for tid in token_ids:
            if tid not in self._records:
                self._records[tid] = TokenRecord(token_id=tid)
                dirty = True
        if dirty:
            self._flush()

    def records(self) -> dict[str, TokenRecord]:
        return dict(self._records)

    def claimable_ids(self) -> list[str]:
        return [tid for tid, r in self._records.items() if r.status == Status.AVAILABLE]

    def all_terminal(self) -> bool:
        return all(r.status in TERMINAL for r in self._records.values())

    def claim(self, token_id: str, log_path: str) -> None:
        rec = self._records[token_id]
        rec.status = Status.IN_FLIGHT
        rec.claimed_at = _now_iso()
        rec.log_path = log_path
        rec.finished_at = None
        rec.exit_code = None
        self._flush()

    def mark_ok(self, token_id: str, exit_code: int) -> None:
        rec = self._records[token_id]
        rec.status = Status.USED_OK
        rec.exit_code = exit_code
        rec.finished_at = _now_iso()
        self._flush()

    def mark_failed(self, token_id: str, exit_code: int) -> None:
        rec = self._records[token_id]
        rec.status = Status.USED_FAILED
        rec.exit_code = exit_code
        rec.finished_at = _now_iso()
        self._flush()

    def mark_aborted(self, token_id: str) -> None:
        rec = self._records[token_id]
        rec.status = Status.USED_ABORTED
        rec.finished_at = _now_iso()
        self._flush()

    def counts(self) -> dict[str, int]:
        out = {s.value: 0 for s in Status}
        for rec in self._records.values():
            out[rec.status.value] += 1
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_state.py -v`

Expected: all 11 tests PASS.

- [ ] **Step 5: Commit**

```powershell
git -C E:\code\auto-run-prl add src/modal_orchestrator/state.py tests/test_state.py
git -C E:\code\auto-run-prl commit -m "feat(state): persistent token state with atomic writes and resume"
```

---

## Task 3: Subprocess runner (`runner.py`)

**Goal:** Launch one Modal subprocess for a given `TokenPair` and `config_path`, with `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` injected into the child's env. Stream stdout+stderr to a log file. Return a `RunResult` with the exit code. Support graceful cancellation.

The command is built from a template: a list of strings where the literal `"{config}"` is replaced with the config path. Default: `["modal", "run", "{config}"]`. Tests use `[sys.executable, str(FAKE_RUNNER), "{config}"]`.

**Files:**
- Create: `E:\code\auto-run-prl\src\modal_orchestrator\runner.py`
- Create: `E:\code\auto-run-prl\tests\test_runner.py`

- [ ] **Step 1: Write failing tests**

File: `E:\code\auto-run-prl\tests\test_runner.py`

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_runner.py -v`

Expected: 6 errors, `ModuleNotFoundError: No module named 'modal_orchestrator.runner'`.

- [ ] **Step 3: Write minimal implementation**

File: `E:\code\auto-run-prl\src\modal_orchestrator\runner.py`

```python
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
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
            raise

    return RunResult(token=token, exit_code=exit_code, log_path=log_path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_runner.py -v`

Expected: all 6 tests PASS.

If `test_can_be_cancelled_via_asyncio_task` is flaky on Windows due to ProactorEventLoop subprocess termination semantics, give the child 0.5s to start (already in test) and bump the kill-grace timeout in `run_one` to 15s. If still flaky, mark that one test `@pytest.mark.skipif(sys.platform == "win32", reason="Proactor subprocess cancel is best-effort")` — but try first without skipping.

- [ ] **Step 5: Commit**

```powershell
git -C E:\code\auto-run-prl add src/modal_orchestrator/runner.py tests/test_runner.py
git -C E:\code\auto-run-prl commit -m "feat(runner): subprocess launcher with per-token env isolation"
```

---

## Task 4: Async scheduler (`scheduler.py`)

**Goal:** Given a token pool (already loaded into `StateStore`), a `cmd_template`, a `config_path`, and a `max_parallel` cap: continuously claim tokens from the store, dispatch them through `run_one`, and record outcomes. Handle `KeyboardInterrupt` by stopping new dispatch and waiting (with a grace period) for in-flight tasks to finish, marking any that don't finish in time as aborted. Returns when the store reports `all_terminal()`.

**Files:**
- Create: `E:\code\auto-run-prl\src\modal_orchestrator\scheduler.py`
- Create: `E:\code\auto-run-prl\tests\test_scheduler.py`

- [ ] **Step 1: Write failing tests**

File: `E:\code\auto-run-prl\tests\test_scheduler.py`

```python
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
    # Two will fail (return code 5), one will succeed.
    store = build_store(tmp_path, ["ak-fail-1", "ak-ok", "ak-fail-2"])
    pool = token_pool(["ak-fail-1", "ak-ok", "ak-fail-2"])

    # Force per-token exit codes via env vars routed through the fake runner.
    # We achieve this by giving the scheduler a custom per-token env hook.
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
    # Generous bounds to avoid CI flakiness; serial would be ~4s.
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
    # ak-1 should still have its old state (untouched).
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
    # All three were dispatched in parallel; all three should be aborted.
    statuses = {r.status for r in recs.values()}
    assert Status.USED_ABORTED in statuses
    assert store.all_terminal()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_scheduler.py -v`

Expected: 5 errors, `ModuleNotFoundError: No module named 'modal_orchestrator.scheduler'`.

- [ ] **Step 3: Write minimal implementation**

File: `E:\code\auto-run-prl\src\modal_orchestrator\scheduler.py`

```python
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

        # Snapshot claimable IDs at start; the store may have terminal records
        # from a previous run that we must not touch.
        claimable = list(self.store.claimable_ids())
        for tid in claimable:
            token = self._tokens_by_id.get(tid)
            if token is None:
                # State file mentions a token that isn't in the loaded pool;
                # skip safely (don't mutate it).
                continue
            task = asyncio.create_task(self._worker(token), name=f"worker-{tid}")
            self._in_flight[tid] = task

        if not self._in_flight:
            return

        shutdown_task = asyncio.create_task(self._shutdown.wait(), name="shutdown")
        worker_tasks = list(self._in_flight.values())

        try:
            done, pending = await asyncio.wait(
                worker_tasks + [shutdown_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            if self._shutdown.is_set():
                await self._graceful_shutdown(worker_tasks)
            else:
                # Either all workers finished, or one did and others are still
                # running. Wait for the rest.
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_scheduler.py -v`

Expected: all 5 tests PASS. The `test_semaphore_caps_concurrency` test has generous wall-clock bounds (1.5s–3.5s) to absorb subprocess startup overhead on Windows.

If `test_request_shutdown_stops_dispatch_and_marks_in_flight_aborted` is the only failure and the message is about a worker finishing as `USED_OK` instead of `USED_ABORTED`, increase `FAKE_SLEEP` from `10` to `30` in that test — the subprocess may be racing to completion before the shutdown trigger fires.

- [ ] **Step 5: Commit**

```powershell
git -C E:\code\auto-run-prl add src/modal_orchestrator/scheduler.py tests/test_scheduler.py
git -C E:\code\auto-run-prl commit -m "feat(scheduler): async pool with semaphore and graceful shutdown"
```

---

## Task 5: CLI (`cli.py`, `__main__.py`)

**Goal:** Wire `tokens.py`, `state.py`, `runner.py`, and `scheduler.py` together behind a `python -m modal_orchestrator` entry point. Two subcommands:
- `run` — execute the orchestration loop.
- `status` — print current counts and exit.

Handle `KeyboardInterrupt` at the top level by calling `Scheduler.request_shutdown()`.

**Files:**
- Create: `E:\code\auto-run-prl\src\modal_orchestrator\__main__.py`
- Create: `E:\code\auto-run-prl\src\modal_orchestrator\cli.py`
- Create: `E:\code\auto-run-prl\tests\test_cli.py`
- Create: `E:\code\auto-run-prl\tokens.example.csv`

- [ ] **Step 1: Write failing tests**

File: `E:\code\auto-run-prl\tests\test_cli.py`

```python
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

    # First run to populate state.
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
    # Log file should exist exactly once; second run should not re-dispatch.
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v`

Expected: 4 failures — the CLI module doesn't exist yet, so `python -m modal_orchestrator` exits non-zero with `No module named modal_orchestrator.__main__`.

- [ ] **Step 3: Write `__main__.py`**

File: `E:\code\auto-run-prl\src\modal_orchestrator\__main__.py`

```python
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Write `cli.py`**

File: `E:\code\auto-run-prl\src\modal_orchestrator\cli.py`

```python
"""Command-line entry point: run | status."""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

from .scheduler import Scheduler, SchedulerConfig
from .state import StateStore, Status
from .tokens import load_tokens, MalformedTokenLine

DEFAULT_CMD_TEMPLATE = ["modal", "run", "{config}"]


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="modal-orchestrator",
        description="Run a Modal workload across many workspace tokens.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("run", help="Run the orchestrator over a token pool.")
    pr.add_argument("--tokens", required=True, type=Path,
                    help="Path to a CSV file of token_id,token_secret pairs.")
    pr.add_argument("--config", required=True,
                    help="Path or module reference passed to `modal run`.")
    pr.add_argument("--state", default=Path("state.json"), type=Path,
                    help="Path to the per-token JSON state file (default: ./state.json).")
    pr.add_argument("--logs", default=Path("logs"), type=Path,
                    help="Directory for per-token log files (default: ./logs).")
    pr.add_argument("--max-parallel", type=int, default=20,
                    help="Maximum number of concurrent subprocesses (default: 20).")
    pr.add_argument("--cmd", action="append", default=None,
                    help=("Override the launch command. Each --cmd appends one argv "
                          "element. Use '{config}' as a placeholder for --config. "
                          "Default: ['modal', 'run', '{config}']."))
    pr.add_argument("--retry-aborted", action="store_true",
                    help="On resume, treat in_flight/aborted tokens as available again.")
    pr.add_argument("--shutdown-grace", type=float, default=60.0,
                    help="Seconds to wait for in-flight workers on Ctrl+C (default: 60).")
    pr.add_argument("-v", "--verbose", action="store_true")

    ps = sub.add_parser("status", help="Print current counts from a state file.")
    ps.add_argument("--state", default=Path("state.json"), type=Path)
    return p


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def _run_async(args: argparse.Namespace) -> int:
    try:
        tokens = load_tokens(args.tokens)
    except FileNotFoundError:
        print(f"tokens file not found: {args.tokens}", file=sys.stderr)
        return 2
    except MalformedTokenLine as e:
        print(f"malformed tokens file: {e}", file=sys.stderr)
        return 2

    if not tokens:
        print("token file is empty; nothing to do", file=sys.stderr)
        return 2

    store = StateStore(args.state, retry_aborted=args.retry_aborted)
    store.ensure_available([t.token_id for t in tokens])

    cmd_template = args.cmd if args.cmd else DEFAULT_CMD_TEMPLATE

    scheduler = Scheduler(
        store=store,
        tokens=tokens,
        config=SchedulerConfig(
            config_path=args.config,
            cmd_template=cmd_template,
            log_dir=args.logs,
            max_parallel=args.max_parallel,
            shutdown_grace_seconds=args.shutdown_grace,
        ),
    )

    # Cross-platform Ctrl+C: install a signal handler that asks the scheduler
    # to wind down. asyncio.run on Windows would otherwise convert SIGINT into
    # CancelledError, skipping the graceful-shutdown grace period.
    loop = asyncio.get_running_loop()
    shutdown_announced = False

    def _on_sigint() -> None:
        nonlocal shutdown_announced
        if not shutdown_announced:
            print(
                "Ctrl+C received; finishing in-flight work then exiting...",
                file=sys.stderr,
            )
            shutdown_announced = True
            scheduler.request_shutdown()

    previous_handler = None
    used_loop_handler = False
    if sys.platform != "win32":
        try:
            loop.add_signal_handler(signal.SIGINT, _on_sigint)
            used_loop_handler = True
        except NotImplementedError:
            previous_handler = signal.signal(
                signal.SIGINT,
                lambda s, f: loop.call_soon_threadsafe(_on_sigint),
            )
    else:
        previous_handler = signal.signal(
            signal.SIGINT,
            lambda s, f: loop.call_soon_threadsafe(_on_sigint),
        )

    try:
        await scheduler.run()
    finally:
        if used_loop_handler:
            try:
                loop.remove_signal_handler(signal.SIGINT)
            except (NotImplementedError, RuntimeError):
                pass
        elif previous_handler is not None:
            try:
                signal.signal(signal.SIGINT, previous_handler)
            except (ValueError, TypeError):
                pass

    counts = store.counts()
    print("Final counts:")
    for status, n in counts.items():
        if n:
            print(f"  {status}: {n}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.cmd == "run":
        _setup_logging(args.verbose)
        try:
            return asyncio.run(_run_async(args))
        except KeyboardInterrupt:
            # asyncio.run propagates KeyboardInterrupt before we can catch it.
            print("interrupted", file=sys.stderr)
            return 130
    elif args.cmd == "status":
        if not args.state.exists():
            print(f"state file not found: {args.state}", file=sys.stderr)
            return 2
        store = StateStore(args.state)
        counts = store.counts()
        total = sum(counts.values())
        print(f"State file: {args.state}")
        print(f"Total tokens: {total}")
        for status, n in counts.items():
            print(f"  {status}: {n}")
        return 0
    else:
        parser.print_help()
        return 2
```

- [ ] **Step 5: Create `tokens.example.csv`**

File: `E:\code\auto-run-prl\tokens.example.csv`

```
# Modal workspace tokens — one per line, comma-separated.
# Copy this file to tokens.csv and fill in real values.
# The orchestrator will skip blank lines and # comments.
# token_id,token_secret
ak-EXAMPLE1234567890,as-EXAMPLE0987654321
ak-EXAMPLE2345678901,as-EXAMPLE1098765432
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_cli.py -v`

Expected: all 4 tests PASS. Each test invokes `python -m modal_orchestrator` as a subprocess, so this exercises the full chain.

- [ ] **Step 7: Run the full test suite**

Run: `pytest`

Expected: all tests PASS (~34 tests across all 5 modules).

- [ ] **Step 8: Commit**

```powershell
git -C E:\code\auto-run-prl add src/modal_orchestrator/__main__.py src/modal_orchestrator/cli.py tests/test_cli.py tokens.example.csv
git -C E:\code\auto-run-prl commit -m "feat(cli): run and status subcommands"
```

---

## Task 6: End-to-end manual smoke test

**Goal:** Walk through the orchestrator one time end-to-end without Modal involved, to confirm the wiring really works the way the unit tests claim. No automated test added — this is a one-shot human verification step.

**Files:** none (uses existing fake_runner).

- [ ] **Step 1: Create a synthetic token pool**

```powershell
cd E:\code\auto-run-prl
@"
ak-smoke-1,as-smoke-1
ak-smoke-2,as-smoke-2
ak-smoke-3,as-smoke-3
ak-smoke-4,as-smoke-4
ak-smoke-5,as-smoke-5
"@ | Set-Content -Path .\smoke-tokens.csv -Encoding utf8
```

- [ ] **Step 2: Run the orchestrator against the fake runner**

```powershell
python -m modal_orchestrator run `
    --tokens .\smoke-tokens.csv `
    --config dummy-config.py `
    --state .\smoke-state.json `
    --logs .\smoke-logs `
    --max-parallel 3 `
    --cmd $(Get-Command python | Select-Object -ExpandProperty Source) `
    --cmd .\tests\fixtures\fake_runner.py `
    --cmd "{config}"
```

Expected stdout: `Final counts:` followed by `used_ok: 5`. Wall time should be a couple of seconds.

- [ ] **Step 3: Verify state file and logs**

```powershell
Get-Content .\smoke-state.json
Get-ChildItem .\smoke-logs
Get-Content (Get-ChildItem .\smoke-logs)[0]
```

Expected: state.json shows 5 `used_ok` records; smoke-logs has 5 `.log` files; each log contains `MODAL_TOKEN_ID=ak-smoke-N` and `MODAL_TOKEN_SECRET=as-smoke-N`.

- [ ] **Step 4: Test resume**

```powershell
python -m modal_orchestrator run `
    --tokens .\smoke-tokens.csv `
    --config dummy-config.py `
    --state .\smoke-state.json `
    --logs .\smoke-logs `
    --cmd $(Get-Command python | Select-Object -ExpandProperty Source) `
    --cmd .\tests\fixtures\fake_runner.py `
    --cmd "{config}"
```

Expected: completes in well under a second, output shows `used_ok: 5` and no new log files appeared (re-running does not re-dispatch terminal tokens).

- [ ] **Step 5: Test Ctrl+C**

Start a long run:

```powershell
@"
ak-slow-1,as-slow-1
ak-slow-2,as-slow-2
ak-slow-3,as-slow-3
"@ | Set-Content -Path .\slow-tokens.csv -Encoding utf8

$env:FAKE_SLEEP = "10"   # makes the fake runner sleep 10s per token
python -m modal_orchestrator run `
    --tokens .\slow-tokens.csv `
    --config dummy-config.py `
    --state .\slow-state.json `
    --logs .\slow-logs `
    --max-parallel 3 `
    --shutdown-grace 2 `
    --cmd $(Get-Command python | Select-Object -ExpandProperty Source) `
    --cmd .\tests\fixtures\fake_runner.py `
    --cmd "{config}"
```

While it's running, hit `Ctrl+C` once. Expected: prints `Ctrl+C received; shutting down...`, waits up to 2s, then exits. Inspect `slow-state.json`:

```powershell
Get-Content .\slow-state.json
$env:FAKE_SLEEP = ""
```

Expected: all three tokens have a terminal status (likely `used_aborted`). No tokens left as `in_flight`.

- [ ] **Step 6: Clean up smoke artifacts and commit nothing**

```powershell
Remove-Item .\smoke-tokens.csv, .\smoke-state.json, .\slow-tokens.csv, .\slow-state.json -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force .\smoke-logs, .\slow-logs -ErrorAction SilentlyContinue
```

No commit. This task is verification only.

---

## Task 7: README

**Files:**
- Create: `E:\code\auto-run-prl\README.md`

- [ ] **Step 1: Write README**

File: `E:\code\auto-run-prl\README.md`

````markdown
# modal-orchestrator

Run a Modal GPU workload across many Modal workspace tokens in parallel. Each
token runs the same config file once on a fresh container; per-token state is
persisted to disk so the run is resumable.

## When to use this

You have N Modal workspace tokens (each with its own free credit pool) and a
single Modal config file that defines a `@app.function(gpu="H100")`. You want
to launch the same workload on as many workspaces as possible, run them until
each token's credit is exhausted (or each finishes successfully), and not have
to babysit the rotation.

## Install

```powershell
git clone <your-fork-url> auto-run-prl
cd auto-run-prl
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

You also need the modal CLI on PATH:

```powershell
modal --version
```

(it's installed by `pip install -e .` as a transitive dependency).

## Token file format

`tokens.csv`:

```
# Lines starting with # are skipped.
# Blank lines are skipped.
# An optional header row "token_id,token_secret" is recognized and skipped.
ak-XXXXXXXX,as-YYYYYYYY
ak-AAAAAAAA,as-BBBBBBBB
```

Duplicate `token_id`s are deduplicated (first wins).

## Run

```powershell
python -m modal_orchestrator run `
    --tokens tokens.csv `
    --config path\to\your_modal_app.py `
    --max-parallel 20
```

Default state file is `./state.json` and default log directory is `./logs/`.
Each token gets one log file at `logs/<token-id>.log` capturing the full
subprocess stdout+stderr.

To use a custom launch command (default is `modal run {config}`):

```powershell
python -m modal_orchestrator run `
    --tokens tokens.csv `
    --config myapp `
    --cmd modal --cmd run --cmd -m --cmd "{config}::entrypoint"
```

Each `--cmd` appends one argv element. The literal `{config}` is replaced with
the value of `--config`.

## Resume

Re-running the command with the same `--state` file picks up where the previous
run left off. Tokens in terminal states (`used_ok`, `used_failed`,
`used_aborted`) are not retouched. Tokens that were `in_flight` at the time of
a crash are marked `used_aborted` (we cannot tell whether the previous run
actually consumed credit, so the safe default is to not retry). Pass
`--retry-aborted` to override that: in-flight tokens are reset to `available`.

## Status

```powershell
python -m modal_orchestrator status --state state.json
```

Prints the count of tokens in each status.

## Failure model

Modal's free credit is around $30 per workspace and runs out after roughly 1–2
hours of H100 time. When credit is exhausted, `modal run` exits non-zero with
an error in its log; the orchestrator records `used_failed` and moves on.
There is no retry — a failed token stays failed for the lifetime of the
state file.

## Shutdown

Ctrl+C stops dispatching new tokens and waits up to `--shutdown-grace` seconds
(default 60) for in-flight subprocesses to terminate. Anything still running
after that is force-killed and marked `used_aborted`. The state file is
written after every transition so an unclean kill still leaves resumable state.

## Layout

- `src/modal_orchestrator/tokens.py` — load CSV
- `src/modal_orchestrator/state.py` — JSON state file, atomic writes
- `src/modal_orchestrator/runner.py` — launch one subprocess per token
- `src/modal_orchestrator/scheduler.py` — async pool, semaphore, signals
- `src/modal_orchestrator/cli.py` — argparse entry point

## Tests

```powershell
pytest
```

Tests use a fake `modal` script (`tests/fixtures/fake_runner.py`) so they
don't touch Modal's servers.
````

- [ ] **Step 2: Commit**

```powershell
git -C E:\code\auto-run-prl add README.md
git -C E:\code\auto-run-prl commit -m "docs: README with install, run, resume, and shutdown"
```

---

## Done

After Task 7 you have a working, tested orchestrator that:

- loads hundreds-to-thousands of Modal tokens from a CSV file;
- launches one isolated `modal run` subprocess per token with `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET` injected into the child's env;
- runs up to `--max-parallel` subprocesses concurrently;
- persists per-token status (`available`, `in_flight`, `used_ok`, `used_failed`, `used_aborted`) to `state.json` after every transition;
- resumes a previous run by skipping any token already in a terminal state;
- handles Ctrl+C by stopping new dispatch and giving in-flight workers a grace period;
- terminates when every token in the pool has reached a terminal status.

## Future work (not in scope)

These were considered and explicitly deferred. Do not add them unless asked.

- Adaptive concurrency (back off on Modal 429s).
- Output collection / aggregation. The user said the config handles its own outputs.
- Per-shard parameters. The user confirmed pure replication — no shard_id passed.
- Token-validity pre-check (`modal config show` per token before burning credit).
- Token-pool top-up while a run is in progress (currently the pool is fixed at start).
- Anything that requires the user's actual workload type to be known. The
  orchestrator is workload-agnostic by design.
