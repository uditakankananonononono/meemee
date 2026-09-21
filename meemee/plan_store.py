from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .types import Plan, PlanStep


class PlanStore:
    """Versioned, durable plans with optimistic concurrency and validated DAG edits."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.db:
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS plans (
                    id TEXT PRIMARY KEY, goal TEXT NOT NULL, version INTEGER NOT NULL,
                    document TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS plan_history (
                    plan_id TEXT NOT NULL, version INTEGER NOT NULL, document TEXT NOT NULL,
                    reason TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY(plan_id, version)
                );
            """)

    def create(self, plan: Plan) -> dict[str, Any]:
        ident, now = uuid.uuid4().hex, datetime.now(timezone.utc).isoformat()
        document = plan.model_dump_json()
        with self.lock, self.db:
            self.db.execute("INSERT INTO plans VALUES(?,?,?,?,?,?)", (ident, plan.goal, 1, document, now, now))
            self.db.execute("INSERT INTO plan_history VALUES(?,?,?,?,?)", (ident, 1, document, "created", now))
        return self.get(ident)

    def get(self, ident: str) -> dict[str, Any]:
        with self.lock:
            row = self.db.execute("SELECT * FROM plans WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        result = dict(row)
        result["plan"] = Plan.model_validate_json(result.pop("document"))
        return result

    def replace(self, ident: str, plan: Plan, expected_version: int, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("plan change reason is required")
        now, document = datetime.now(timezone.utc).isoformat(), plan.model_dump_json()
        with self.lock, self.db:
            changed = self.db.execute(
                "UPDATE plans SET goal=?,version=version+1,document=?,updated_at=? WHERE id=? AND version=?",
                (plan.goal, document, now, ident, expected_version),
            ).rowcount
            if not changed:
                actual = self.db.execute("SELECT version FROM plans WHERE id=?", (ident,)).fetchone()
                if actual is None:
                    raise KeyError(ident)
                raise ValueError(f"version conflict: expected {expected_version}, actual {actual['version']}")
            self.db.execute("INSERT INTO plan_history VALUES(?,?,?,?,?)", (ident, expected_version + 1, document, reason, now))
        return self.get(ident)

    def update_status(self, ident: str, step_id: str, status: str, expected_version: int) -> dict[str, Any]:
        current = self.get(ident)
        steps = []
        found = False
        for step in current["plan"].steps:
            payload = step.model_dump()
            if step.id == step_id:
                payload["status"] = status
                found = True
            steps.append(PlanStep.model_validate(payload))
        if not found:
            raise KeyError(step_id)
        return self.replace(ident, Plan(goal=current["plan"].goal, steps=steps), expected_version, f"status {step_id} -> {status}")

    def history(self, ident: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("SELECT version,reason,created_at,document FROM plan_history WHERE plan_id=? ORDER BY version", (ident,)).fetchall()
        return [{**dict(row), "plan": json.loads(row["document"])} for row in rows]
