from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class SourceHealthStore:
    """Persistent source check ledger with freshness and failure state."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.db:
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS source_health(
                  owner_id TEXT NOT NULL, source_id TEXT NOT NULL,
                  last_attempt_at TEXT NOT NULL, last_success_at TEXT,
                  last_cursor TEXT, consecutive_failures INTEGER NOT NULL DEFAULT 0,
                  total_successes INTEGER NOT NULL DEFAULT 0, total_failures INTEGER NOT NULL DEFAULT 0,
                  latency_ms REAL, last_error TEXT, metadata TEXT NOT NULL DEFAULT '{}',
                  PRIMARY KEY(owner_id,source_id));
                CREATE TABLE IF NOT EXISTS source_health_checks(
                  id INTEGER PRIMARY KEY, owner_id TEXT NOT NULL, source_id TEXT NOT NULL,
                  checked_at TEXT NOT NULL, ok INTEGER NOT NULL, latency_ms REAL,
                  cursor TEXT, error TEXT, metadata TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS source_health_checks_source
                  ON source_health_checks(owner_id,source_id,checked_at DESC);
            """)

    def record(
        self,
        owner_id: str,
        source_id: str,
        *,
        ok: bool,
        latency_ms: float | None = None,
        cursor: str | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
        checked_at: str | None = None,
    ) -> dict[str, Any]:
        if not owner_id or not source_id:
            raise ValueError("owner_id and source_id are required")
        if ok and error:
            raise ValueError("successful check cannot have an error")
        if latency_ms is not None and latency_ms < 0:
            raise ValueError("latency_ms cannot be negative")
        stamp = checked_at or datetime.now(timezone.utc).isoformat()
        encoded = json.dumps(metadata or {}, sort_keys=True)
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO source_health_checks(owner_id,source_id,checked_at,ok,latency_ms,cursor,error,metadata) VALUES(?,?,?,?,?,?,?,?)",
                (owner_id, source_id, stamp, int(ok), latency_ms, cursor, error, encoded),
            )
            self.db.execute(
                """
                INSERT INTO source_health(owner_id,source_id,last_attempt_at,last_success_at,last_cursor,
                  consecutive_failures,total_successes,total_failures,latency_ms,last_error,metadata)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(owner_id,source_id) DO UPDATE SET
                  last_attempt_at=excluded.last_attempt_at,
                  last_success_at=CASE WHEN excluded.last_success_at IS NOT NULL THEN excluded.last_success_at ELSE source_health.last_success_at END,
                  last_cursor=CASE WHEN excluded.last_cursor IS NOT NULL THEN excluded.last_cursor ELSE source_health.last_cursor END,
                  consecutive_failures=CASE WHEN excluded.last_success_at IS NOT NULL THEN 0 ELSE source_health.consecutive_failures+1 END,
                  total_successes=source_health.total_successes+excluded.total_successes,
                  total_failures=source_health.total_failures+excluded.total_failures,
                  latency_ms=excluded.latency_ms,last_error=excluded.last_error,metadata=excluded.metadata
            """,
                (
                    owner_id,
                    source_id,
                    stamp,
                    stamp if ok else None,
                    cursor,
                    0 if ok else 1,
                    1 if ok else 0,
                    0 if ok else 1,
                    latency_ms,
                    None if ok else (error or "unknown error"),
                    encoded,
                ),
            )
        return self.get(owner_id, source_id) or {}

    def get(self, owner_id: str, source_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM source_health WHERE owner_id=? AND source_id=?",
                (owner_id, source_id),
            ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["metadata"] = json.loads(result["metadata"])
        return result

    def status(
        self,
        owner_id: str,
        source_id: str,
        *,
        stale_after: int = 900,
        failure_threshold: int = 3,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        row = self.get(owner_id, source_id)
        if row is None:
            return {
                "owner_id": owner_id,
                "source_id": source_id,
                "status": "unknown",
                "reason": "never_checked",
            }
        clock = now or datetime.now(timezone.utc)
        success = datetime.fromisoformat(row["last_success_at"]) if row["last_success_at"] else None
        if row["consecutive_failures"] >= failure_threshold:
            state, reason = "down", "failure_threshold"
        elif success is None or clock - success > timedelta(seconds=stale_after):
            state, reason = "stale", "no_recent_success"
        elif row["consecutive_failures"]:
            state, reason = "degraded", "recent_failure"
        else:
            state, reason = "healthy", "recent_success"
        return {**row, "status": state, "reason": reason}

    def list_status(self, owner_id: str, **policy: Any) -> list[dict[str, Any]]:
        with self.lock:
            ids = [
                row[0]
                for row in self.db.execute(
                    "SELECT source_id FROM source_health WHERE owner_id=? ORDER BY source_id",
                    (owner_id,),
                )
            ]
        return [self.status(owner_id, source_id, **policy) for source_id in ids]

    def history(self, owner_id: str, source_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM source_health_checks WHERE owner_id=? AND source_id=? ORDER BY id DESC LIMIT ?",
                (owner_id, source_id, max(1, min(limit, 500))),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["ok"] = bool(item["ok"])
            item["metadata"] = json.loads(item["metadata"])
            result.append(item)
        return result
