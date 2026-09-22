from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


class ApprovalStore:
    """Persistent, revocable, expiring per-principal grants for exact tool names."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock, self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS tool_approvals (
                principal TEXT NOT NULL, tool TEXT NOT NULL, granted_at TEXT NOT NULL,
                expires_at TEXT, revoked_at TEXT, granted_by TEXT NOT NULL,
                PRIMARY KEY(principal, tool)
            )""")

    def grant(self, principal: str, tool: str, granted_by: str, expires_at: str | None = None) -> None:
        if not principal or not tool or not granted_by:
            raise ValueError("principal, tool and granted_by are required")
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO tool_approvals VALUES(?,?,?,?,NULL,?) ON CONFLICT(principal,tool) DO UPDATE SET granted_at=excluded.granted_at,expires_at=excluded.expires_at,revoked_at=NULL,granted_by=excluded.granted_by",
                (principal, tool, now, expires_at, granted_by),
            )

    def allows(self, principal: str, tool: str, now: datetime | None = None) -> bool:
        current = (now or datetime.now(timezone.utc)).isoformat()
        with self.lock:
            row = self.db.execute(
                "SELECT 1 FROM tool_approvals WHERE principal=? AND tool=? AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>?)",
                (principal, tool, current),
            ).fetchone()
        return row is not None

    def revoke(self, principal: str, tool: str) -> bool:
        with self.lock, self.db:
            changed = self.db.execute(
                "UPDATE tool_approvals SET revoked_at=? WHERE principal=? AND tool=? AND revoked_at IS NULL",
                (datetime.now(timezone.utc).isoformat(), principal, tool),
            ).rowcount
        return bool(changed)

    def active_count(self, principal: str, now: datetime | None = None) -> int:
        current = (now or datetime.now(timezone.utc)).isoformat()
        with self.lock:
            return int(self.db.execute(
                "SELECT count(*) FROM tool_approvals WHERE principal=? AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>?)",
                (principal, current),
            ).fetchone()[0])

    def list(self, principal: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT principal,tool,granted_at,expires_at,revoked_at,granted_by FROM tool_approvals WHERE principal=? ORDER BY tool",
                (principal,),
            ).fetchall()
        return [dict(row) for row in rows]
