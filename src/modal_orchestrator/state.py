"""On-disk JSON state for the token pool.

Atomic writes via tmp+os.replace. On reload, any token left in IN_FLIGHT
is conservatively marked USED_ABORTED (we cannot tell whether the previous
process actually consumed credit, but we must assume it might have, and
re-using the same token risks double-billing the same workspace).
Pass retry_aborted=True to override: in_flight tokens are reset to available.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


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
            logger.warning(
                "state file %s is corrupted; starting from empty state",
                self.path,
            )
            return
        transitioned = False
        for token_id, obj in data.get("tokens", {}).items():
            rec = TokenRecord.from_json(token_id, obj)
            if rec.status == Status.IN_FLIGHT:
                transitioned = True
                if retry_aborted:
                    rec.status = Status.AVAILABLE
                    rec.claimed_at = None
                else:
                    rec.status = Status.USED_ABORTED
                    rec.finished_at = _now_iso()
            self._records[token_id] = rec
        if transitioned:
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
        # Copy values too: TokenRecord fields can be mutated by concurrent
        # workers between when the dashboard requests state and when it
        # finishes serializing, producing half-updated records.
        return {tid: replace(rec) for tid, rec in self._records.items()}

    def claimable_ids(self) -> list[str]:
        return [tid for tid, r in self._records.items() if r.status == Status.AVAILABLE]

    def all_terminal(self) -> bool:
        return all(r.status in TERMINAL for r in self._records.values())

    def _get(self, token_id: str) -> TokenRecord:
        try:
            return self._records[token_id]
        except KeyError:
            raise KeyError(
                f"token {token_id!r} not found in state store"
            ) from None

    def claim(self, token_id: str, log_path: str) -> None:
        rec = self._get(token_id)
        rec.status = Status.IN_FLIGHT
        rec.claimed_at = _now_iso()
        rec.log_path = log_path
        rec.finished_at = None
        rec.exit_code = None
        self._flush()

    def mark_ok(self, token_id: str, exit_code: int) -> None:
        rec = self._get(token_id)
        rec.status = Status.USED_OK
        rec.exit_code = exit_code
        rec.finished_at = _now_iso()
        self._flush()

    def mark_failed(self, token_id: str, exit_code: int) -> None:
        rec = self._get(token_id)
        rec.status = Status.USED_FAILED
        rec.exit_code = exit_code
        rec.finished_at = _now_iso()
        self._flush()

    def mark_aborted(self, token_id: str) -> None:
        rec = self._get(token_id)
        rec.status = Status.USED_ABORTED
        rec.finished_at = _now_iso()
        self._flush()

    def counts(self) -> dict[str, int]:
        out = {s.value: 0 for s in Status}
        for rec in self._records.values():
            out[rec.status.value] += 1
        return out
