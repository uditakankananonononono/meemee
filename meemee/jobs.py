from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema_registry import register_schema


class JobStore:
    """Durable SQLite queue with atomic claims and an append-only event stream."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, goal TEXT NOT NULL, run_at TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('queued','running','done','failed','cancel_requested','cancelled')),
                attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 3,
                result TEXT, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS jobs_due ON jobs(status, run_at);
            CREATE TABLE IF NOT EXISTS job_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS job_events_job ON job_events(job_id, sequence);
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(jobs)")}
        if "principal" not in columns:
            self.db.execute("ALTER TABLE jobs ADD COLUMN principal TEXT")
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS jobs_principal_updated ON jobs(principal,updated_at DESC,id DESC)"
        )
        register_schema(self.db, "jobs", 2, [
            "jobs principal ownership", "job events", "principal updated index"
        ])

    def event(self, ident: str, kind: str, payload: dict[str, Any]) -> None:
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO job_events(job_id,kind,payload,created_at) VALUES(?,?,?,?)",
                (ident, kind, json.dumps(payload), datetime.now(timezone.utc).isoformat()),
            )

    def events(self, ident: str, after: int = 0) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM job_events WHERE job_id=? AND sequence>? ORDER BY sequence",
                (ident, after),
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def enqueue(self, goal: str, run_at: datetime | None = None, max_attempts: int = 3, principal: str | None = None) -> str:
        ident = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        due = (run_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO jobs(id,goal,run_at,status,max_attempts,created_at,updated_at,principal) VALUES(?,?,?,'queued',?,?,?,?)",
                (ident, goal, due, max_attempts, now, now, principal),
            )
        self.event(ident, "queued", {"run_at": due})
        return ident

    def claim(self) -> dict[str, Any] | None:
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                "SELECT * FROM jobs WHERE status='queued' AND run_at<=? ORDER BY run_at,id LIMIT 1",
                (now,),
            ).fetchone()
            if row is None:
                self.db.execute("COMMIT")
                return None
            changed = self.db.execute(
                "UPDATE jobs SET status='running', attempts=attempts+1, updated_at=? WHERE id=? AND status='queued'",
                (now, row["id"]),
            ).rowcount
            self.db.execute("COMMIT")
        if changed:
            self.event(row["id"], "running", {"attempt": row["attempts"] + 1})
        return dict(row) if changed else None

    def finish(self, ident: str, result: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.db:
            self.db.execute(
                "UPDATE jobs SET status='done', result=?, updated_at=? WHERE id=? AND status='running'",
                (json.dumps(result), now, ident),
            )
        self.event(ident, "done", {"result": result})

    def fail(self, ident: str, error: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.db:
            self.db.execute(
                "UPDATE jobs SET status=CASE WHEN attempts<max_attempts THEN 'queued' ELSE 'failed' END, error=?, updated_at=? WHERE id=? AND status='running'",
                (error, now, ident),
            )
        job = self.get(ident)
        self.event(ident, "retry" if job and job["status"] == "queued" else "failed", {"error": error})


    def request_cancel(self, ident: str) -> str | None:
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.db:
            row = self.db.execute("SELECT status FROM jobs WHERE id=?", (ident,)).fetchone()
            if row is None:
                return None
            if row["status"] == "queued":
                target = "cancelled"
            elif row["status"] == "running":
                target = "cancel_requested"
            else:
                return row["status"]
            self.db.execute("UPDATE jobs SET status=?, error='cancelled', updated_at=? WHERE id=?", (target, now, ident))
        self.event(ident, target, {})
        return target

    def cancel_running(self, ident: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.db:
            changed = self.db.execute("UPDATE jobs SET status='cancelled', error='cancelled', updated_at=? WHERE id=? AND status='cancel_requested'", (now, ident)).rowcount
        if changed:
            self.event(ident, "cancelled", {})
        return bool(changed)

    def cancel(self, ident: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.db:
            changed = self.db.execute(
                "UPDATE jobs SET status='failed', error='cancelled', updated_at=? WHERE id=? AND status='queued'",
                (now, ident),
            ).rowcount
        if changed:
            self.event(ident, "cancelled", {})
        return bool(changed)

    def list_for_principal(
        self, principal: str, status: str | None = None, before: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        clauses, parameters = ["principal=?"], [principal]
        if status is not None:
            clauses.append("status=?"); parameters.append(status)
        if before is not None:
            clauses.append("updated_at<?"); parameters.append(before)
        parameters.append(min(max(limit, 1), 500))
        with self.lock:
            rows = self.db.execute(
                f"SELECT * FROM jobs WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC,id DESC LIMIT ?",
                tuple(parameters),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_owned(self, ident: str, principal: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM jobs WHERE id=? AND principal=?", (ident, principal)
            ).fetchone()
        return dict(row) if row else None

    def get(self, ident: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM jobs WHERE id=?", (ident,)).fetchone()
        return dict(row) if row else None
