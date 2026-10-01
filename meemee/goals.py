"""Durable goals and atomic work leasing for persistent agency loops."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

GoalStatus = Literal["pending", "active", "blocked", "completed", "failed", "cancelled"]
_TERMINAL = {"completed", "failed", "cancelled"}
_TRANSITIONS = {
    "pending": {"active", "blocked", "cancelled"},
    "active": {"pending", "blocked", "completed", "failed", "cancelled"},
    "blocked": {"pending", "active", "cancelled"},
    "completed": set(), "failed": set(), "cancelled": set(),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(timezone.utc).isoformat()


class GoalConflict(RuntimeError):
    """The requested mutation conflicts with current durable state."""


class GoalStore:
    """Principal-scoped goal graph with priorities, dependencies and expiring leases."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS agency_goals(
                id TEXT PRIMARY KEY, principal TEXT NOT NULL, description TEXT NOT NULL,
                status TEXT NOT NULL, priority INTEGER NOT NULL, parent_id TEXT,
                context TEXT NOT NULL, outcome TEXT, failure TEXT,
                lease_owner TEXT, lease_until TEXT, version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(parent_id) REFERENCES agency_goals(id)
            );
            CREATE INDEX IF NOT EXISTS agency_goals_ready
                ON agency_goals(principal,status,priority DESC,created_at);
            CREATE TABLE IF NOT EXISTS agency_goal_dependencies(
                goal_id TEXT NOT NULL, dependency_id TEXT NOT NULL,
                PRIMARY KEY(goal_id,dependency_id),
                FOREIGN KEY(goal_id) REFERENCES agency_goals(id),
                FOREIGN KEY(dependency_id) REFERENCES agency_goals(id)
            );
        """)

    def create(self, principal: str, description: str, *, priority: int = 0,
               parent_id: str | None = None, depends_on: list[str] | None = None,
               context: dict[str, Any] | None = None, goal_id: str | None = None) -> dict[str, Any]:
        if not principal.strip() or not description.strip():
            raise ValueError("principal and description are required")
        ident, now = goal_id or uuid.uuid4().hex, _iso()
        dependencies = list(dict.fromkeys(depends_on or []))
        if ident in dependencies: raise ValueError("goal cannot depend on itself")
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                if parent_id: self._owned(principal, parent_id)
                for dependency in dependencies: self._owned(principal, dependency)
                self.db.execute(
                    "INSERT INTO agency_goals VALUES(?,?,?,'pending',?,?,?,NULL,NULL,NULL,NULL,1,?,?)",
                    (ident, principal, description.strip(), priority, parent_id,
                     json.dumps(context or {}, sort_keys=True), now, now),
                )
                self.db.executemany("INSERT INTO agency_goal_dependencies VALUES(?,?)",
                                    ((ident, item) for item in dependencies))
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK"); raise
        return self.get(principal, ident)

    def _owned(self, principal: str, goal_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM agency_goals WHERE principal=? AND id=?", (principal, goal_id)).fetchone()
        if row is None: raise KeyError(goal_id)
        return row

    def get(self, principal: str, goal_id: str) -> dict[str, Any]:
        with self.lock:
            row = self._owned(principal, goal_id)
            dependencies = [r[0] for r in self.db.execute(
                "SELECT dependency_id FROM agency_goal_dependencies WHERE goal_id=? ORDER BY dependency_id", (goal_id,))]
        return self._row(row, dependencies)

    def list(self, principal: str, status: GoalStatus | None = None) -> list[dict[str, Any]]:
        query, args = "SELECT id FROM agency_goals WHERE principal=?", [principal]
        if status: query += " AND status=?"; args.append(status)
        query += " ORDER BY priority DESC,created_at,id"
        with self.lock: ids = [r[0] for r in self.db.execute(query, args)]
        return [self.get(principal, ident) for ident in ids]

    def claim(self, principal: str, worker_id: str, *, lease_seconds: int = 300) -> dict[str, Any] | None:
        if not worker_id.strip() or lease_seconds <= 0: raise ValueError("worker and positive lease are required")
        now, until = _iso(), _iso(_now() + timedelta(seconds=lease_seconds))
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("""
                SELECT g.id FROM agency_goals g
                WHERE g.principal=? AND g.status IN ('pending','active')
                  AND (g.lease_until IS NULL OR g.lease_until<=?)
                  AND NOT EXISTS (
                    SELECT 1 FROM agency_goal_dependencies d JOIN agency_goals dep ON dep.id=d.dependency_id
                    WHERE d.goal_id=g.id AND dep.status!='completed')
                ORDER BY g.priority DESC,g.created_at,g.id LIMIT 1
            """, (principal, now)).fetchone()
            if row is None: self.db.execute("COMMIT"); return None
            changed = self.db.execute("""UPDATE agency_goals SET status='active',lease_owner=?,lease_until=?,
                version=version+1,updated_at=? WHERE id=? AND (lease_until IS NULL OR lease_until<=?)""",
                (worker_id, until, now, row["id"], now)).rowcount
            self.db.execute("COMMIT")
        return self.get(principal, row["id"]) if changed else None

    def renew(self, principal: str, goal_id: str, worker_id: str, lease_seconds: int = 300) -> dict[str, Any]:
        if lease_seconds <= 0: raise ValueError("positive lease is required")
        now, until = _iso(), _iso(_now() + timedelta(seconds=lease_seconds))
        with self.lock, self.db:
            changed = self.db.execute("""UPDATE agency_goals SET lease_until=?,version=version+1,updated_at=?
                WHERE principal=? AND id=? AND status='active' AND lease_owner=? AND lease_until>?""",
                (until, now, principal, goal_id, worker_id, now)).rowcount
        if not changed: raise GoalConflict("goal has no live lease owned by worker")
        return self.get(principal, goal_id)

    def transition(self, principal: str, goal_id: str, status: GoalStatus, *, worker_id: str | None = None,
                   expected_version: int | None = None, outcome: str | None = None,
                   failure: str | None = None) -> dict[str, Any]:
        now = _iso()
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                row = self._owned(principal, goal_id)
                if status not in _TRANSITIONS[row["status"]]:
                    raise GoalConflict(f"invalid transition {row['status']} -> {status}")
                if expected_version is not None and row["version"] != expected_version:
                    raise GoalConflict(f"version conflict: expected {expected_version}, actual {row['version']}")
                if row["lease_owner"] and worker_id != row["lease_owner"]:
                    raise GoalConflict("active lease is owned by another worker")
                if status == "completed":
                    waiting = self.db.execute("""SELECT 1 FROM agency_goal_dependencies d
                        JOIN agency_goals dep ON dep.id=d.dependency_id
                        WHERE d.goal_id=? AND dep.status!='completed' LIMIT 1""", (goal_id,)).fetchone()
                    if waiting: raise GoalConflict("dependencies are not complete")
                clear = status in _TERMINAL or status in {"pending", "blocked"}
                self.db.execute("""UPDATE agency_goals SET status=?,outcome=?,failure=?,lease_owner=?,lease_until=?,
                    version=version+1,updated_at=? WHERE id=?""",
                    (status, outcome, failure, None if clear else row["lease_owner"],
                     None if clear else row["lease_until"], now, goal_id))
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK"); raise
        return self.get(principal, goal_id)

    @staticmethod
    def _row(row: sqlite3.Row, dependencies: list[str]) -> dict[str, Any]:
        result = dict(row); result["context"] = json.loads(result["context"]); result["depends_on"] = dependencies
        return result

    def init_execution(self):
        """Additive local workflow schema; independent of model-generated plans."""
        with self.lock:
            self.db.executescript('''
                PRAGMA busy_timeout=5000;
                CREATE TABLE IF NOT EXISTS agency_progress(goal_id TEXT PRIMARY KEY, step INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS agency_step_grants(goal_id TEXT, step INTEGER, principal TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(goal_id,step));
                CREATE TABLE IF NOT EXISTS agency_notes(goal_id TEXT, step INTEGER, principal TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(goal_id,step));
                CREATE TABLE IF NOT EXISTS agency_execution_events(id INTEGER PRIMARY KEY, goal_id TEXT, principal TEXT NOT NULL, step INTEGER, kind TEXT NOT NULL, detail TEXT NOT NULL, created_at TEXT NOT NULL);
            ''')

    def approve_step(self, principal, goal_id, step):
        import hashlib
        self.init_execution()
        with self.lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                goal = self._owned(principal, goal_id)
                plan = json.loads(goal['context'])['steps']
                action = plan[step]
                if step < 0 or action['kind'] != 'note' or goal['status'] in _TERMINAL:
                    raise ValueError('only a live exact note step may be approved')
                digest = hashlib.sha256(json.dumps(action, sort_keys=True).encode()).hexdigest()
                self.db.execute('INSERT OR REPLACE INTO agency_step_grants VALUES(?,?,?,?)',
                                (goal_id, step, principal, digest))
                self.db.execute("UPDATE agency_goals SET status='pending',lease_owner=NULL,lease_until=NULL WHERE id=? AND status='blocked'", (goal_id,))
                self.db.execute('COMMIT')
            except Exception:
                self.db.execute('ROLLBACK')
                raise

    def revoke_step(self, principal, goal_id, step):
        self.init_execution()
        with self.lock:
            self._owned(principal, goal_id)
            self.db.execute('DELETE FROM agency_step_grants WHERE goal_id=? AND step=? AND principal=?', (goal_id, step, principal))

    def wake(self, principal, goal_id):
        with self.lock:
            self._owned(principal, goal_id)
            self.db.execute("UPDATE agency_goals SET status='pending',lease_owner=NULL,lease_until=NULL WHERE id=? AND status='blocked'", (goal_id,))

    def notes(self, principal):
        self.init_execution()
        with self.lock:
            return [dict(row) for row in self.db.execute('SELECT * FROM agency_notes WHERE principal=? ORDER BY created_at,goal_id,step', (principal,))]

    def delete_owner(self, principal):
        self.init_execution()
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                self.db.execute('DELETE FROM agency_goal_dependencies WHERE goal_id IN (SELECT id FROM agency_goals WHERE principal=?)', (principal,))
                self.db.execute('DELETE FROM agency_progress WHERE goal_id IN (SELECT id FROM agency_goals WHERE principal=?)', (principal,))
                for table in ('agency_step_grants', 'agency_notes', 'agency_execution_events'):
                    self.db.execute(f'DELETE FROM {table} WHERE principal=?', (principal,))
                # Remove child references before deleting the graph.
                self.db.execute('UPDATE agency_goals SET parent_id=NULL WHERE principal=?', (principal,))
                count = self.db.execute('DELETE FROM agency_goals WHERE principal=?', (principal,)).rowcount
                self.db.execute('COMMIT')
                return count
            except Exception:
                self.db.execute('ROLLBACK')
                raise
