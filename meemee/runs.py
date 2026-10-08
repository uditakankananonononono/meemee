from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ._sqlite_guard import close_db_on_init_failure
from .cursors import decode_cursor, encode_cursor
from .schema_registry import register_schema
from .types import RunReport


def _cutoff(value: str) -> str:
    instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    instant = instant if instant.tzinfo else instant.replace(tzinfo=timezone.utc)
    return instant.astimezone(timezone.utc).isoformat()


class RunStore:
    """Principal-owned completed run reports for durable account history."""

    @close_db_on_init_failure
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db=sqlite3.connect(path,check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        self.lock=threading.RLock()
        self.db.execute("PRAGMA busy_timeout=5000")
        deadline = time.monotonic() + 5
        while True:
            try:
                self.db.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                    self.db.close()
                    raise
                time.sleep(0.01)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS runs(
                run_id TEXT PRIMARY KEY, principal TEXT NOT NULL, goal TEXT NOT NULL,
                final TEXT NOT NULL, steps_used INTEGER NOT NULL, tool_results TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS runs_principal_created ON runs(principal,created_at DESC,run_id DESC);
        """)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in self.db.execute("PRAGMA table_info(runs)")}
            if "approvals_required" not in columns:  # older rows read as []
                self.db.execute("ALTER TABLE runs ADD COLUMN approvals_required TEXT NOT NULL DEFAULT '[]'")
        register_schema(self.db, "runs", 2, ["principal owned completed run reports",
                                             "approvals_required refusal list per run"])

    def add(self, principal: str, report: RunReport) -> None:
        results = json.dumps(report.tool_results, allow_nan=False)
        approvals = json.dumps([item.model_dump() for item in report.approvals_required], allow_nan=False)
        with self.lock,self.db:
            self.db.execute(
                "INSERT INTO runs(run_id,principal,goal,final,steps_used,tool_results,created_at,approvals_required) VALUES(?,?,?,?,?,?,?,?)",
                (report.run_id,principal,report.goal,report.final,report.steps_used,results,datetime.now(timezone.utc).isoformat(),
                 approvals),
            )

    def get(self, principal: str, run_id: str) -> dict | None:
        with self.lock:
            row=self.db.execute("SELECT * FROM runs WHERE principal=? AND run_id=?",(principal,run_id)).fetchone()
        return self._row(row) if row else None

    def list(
        self, principal: str, before: str | None = None, limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        query = "SELECT * FROM runs WHERE principal=?"; params: list = [principal]
        if before is not None:
            query += " AND created_at<?"; params.append(_cutoff(before))
        if cursor is not None:
            cursor_time, cursor_id = decode_cursor(cursor)
            cursor_time = _cutoff(cursor_time)
            query += " AND (created_at<? OR (created_at=? AND run_id<?))"
            params.extend((cursor_time, cursor_time, cursor_id))
        page_size = min(max(limit, 1), 500)
        query += " ORDER BY created_at DESC,run_id DESC LIMIT ?"; params.append(page_size + 1)
        with self.lock: rows = self.db.execute(query, tuple(params)).fetchall()
        items = [self._row(row) for row in rows[:page_size]]
        next_cursor = encode_cursor(items[-1]["created_at"], items[-1]["run_id"]) if len(rows) > page_size else None
        return items, next_cursor


    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        result=dict(row); result["tool_results"]=json.loads(result["tool_results"])
        result["approvals_required"]=json.loads(result.get("approvals_required") or "[]")
        result["blocked"]=bool(result["approvals_required"]); return result

    def ping(self) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1").fetchone() is not None

    def run_ids(self, principal: str) -> list[str]:
        with self.lock:
            return [row["run_id"] for row in self.db.execute("SELECT run_id FROM runs WHERE principal=?", (principal,))]

    def delete_principal(self, principal: str) -> int:
        """Hard-delete every completed run report owned by a principal."""
        if not principal:
            raise ValueError("principal is required")
        with self.lock, self.db:
            return self.db.execute("DELETE FROM runs WHERE principal=?", (principal,)).rowcount

