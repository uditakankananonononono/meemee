from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .types import RunReport


class RunStore:
    """Principal-owned completed run reports for durable account history."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db=sqlite3.connect(path,check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        self.lock=threading.RLock()
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS runs(
                run_id TEXT PRIMARY KEY, principal TEXT NOT NULL, goal TEXT NOT NULL,
                final TEXT NOT NULL, steps_used INTEGER NOT NULL, tool_results TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS runs_principal_created ON runs(principal,created_at DESC,run_id DESC);
        """)

    def add(self, principal: str, report: RunReport) -> None:
        with self.lock,self.db:
            self.db.execute(
                "INSERT INTO runs VALUES(?,?,?,?,?,?,?)",
                (report.run_id,principal,report.goal,report.final,report.steps_used,json.dumps(report.tool_results),datetime.now(timezone.utc).isoformat()),
            )

    def get(self, principal: str, run_id: str) -> dict | None:
        with self.lock:
            row=self.db.execute("SELECT * FROM runs WHERE principal=? AND run_id=?",(principal,run_id)).fetchone()
        return self._row(row) if row else None

    def list(self, principal: str, before: str | None=None, limit: int=100) -> list[dict]:
        query="SELECT * FROM runs WHERE principal=?"; params:list=[principal]
        if before is not None: query+=" AND created_at<?"; params.append(before)
        query+=" ORDER BY created_at DESC,run_id DESC LIMIT ?"; params.append(min(max(limit,1),500))
        with self.lock: rows=self.db.execute(query,tuple(params)).fetchall()
        return [self._row(row) for row in rows]

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        result=dict(row); result["tool_results"]=json.loads(result["tool_results"]); return result
