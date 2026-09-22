"""Append-only resumable checkpoints for persistent agent executions."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _canonical(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class CheckpointConflict(RuntimeError):
    pass


class CheckpointStore:
    """Per-tenant run journals with monotonic sequence and idempotent writes."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS agency_checkpoints(
                id TEXT PRIMARY KEY, principal TEXT NOT NULL, run_id TEXT NOT NULL,
                goal_id TEXT NOT NULL, sequence INTEGER NOT NULL, phase TEXT NOT NULL,
                state TEXT NOT NULL, state_sha256 TEXT NOT NULL, idempotency_key TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(principal,run_id,sequence), UNIQUE(principal,run_id,idempotency_key)
            );
            CREATE INDEX IF NOT EXISTS agency_checkpoints_latest
                ON agency_checkpoints(principal,run_id,sequence DESC);
        """)

    def save(self, principal: str, run_id: str, goal_id: str, phase: str,
             state: dict[str, Any], *, expected_sequence: int | None = None,
             idempotency_key: str | None = None) -> dict[str, Any]:
        if not all(x.strip() for x in (principal, run_id, goal_id, phase)):
            raise ValueError("principal, run, goal and phase are required")
        document = _canonical(state); digest = hashlib.sha256(document.encode()).hexdigest()
        now, ident = datetime.now(timezone.utc).isoformat(), uuid.uuid4().hex
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                if idempotency_key:
                    existing = self.db.execute("""SELECT * FROM agency_checkpoints
                        WHERE principal=? AND run_id=? AND idempotency_key=?""",
                        (principal, run_id, idempotency_key)).fetchone()
                    if existing:
                        if (existing["goal_id"], existing["phase"], existing["state_sha256"]) != (goal_id, phase, digest):
                            raise CheckpointConflict("idempotency key was used for different checkpoint content")
                        self.db.execute("COMMIT"); return self._row(existing)
                latest = self.db.execute("""SELECT sequence FROM agency_checkpoints
                    WHERE principal=? AND run_id=? ORDER BY sequence DESC LIMIT 1""",
                    (principal, run_id)).fetchone()
                current = latest["sequence"] if latest else 0
                if expected_sequence is not None and expected_sequence != current:
                    raise CheckpointConflict(f"sequence conflict: expected {expected_sequence}, actual {current}")
                sequence = current + 1
                self.db.execute("INSERT INTO agency_checkpoints VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (ident, principal, run_id, goal_id, sequence, phase, document, digest, idempotency_key, now))
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK"); raise
        return self.get(principal, run_id, sequence)

    def latest(self, principal: str, run_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("""SELECT * FROM agency_checkpoints WHERE principal=? AND run_id=?
                ORDER BY sequence DESC LIMIT 1""", (principal, run_id)).fetchone()
        return self._row(row) if row else None

    def get(self, principal: str, run_id: str, sequence: int) -> dict[str, Any]:
        with self.lock:
            row = self.db.execute("""SELECT * FROM agency_checkpoints
                WHERE principal=? AND run_id=? AND sequence=?""", (principal, run_id, sequence)).fetchone()
        if row is None: raise KeyError((run_id, sequence))
        return self._row(row)

    def history(self, principal: str, run_id: str, *, after: int = 0) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute("""SELECT * FROM agency_checkpoints
                WHERE principal=? AND run_id=? AND sequence>? ORDER BY sequence""",
                (principal, run_id, after)).fetchall()
        return [self._row(row) for row in rows]

    def resume(self, principal: str, run_id: str) -> dict[str, Any]:
        checkpoint = self.latest(principal, run_id)
        if checkpoint is None: raise KeyError(run_id)
        return {"run_id": run_id, "goal_id": checkpoint["goal_id"],
                "next_sequence": checkpoint["sequence"] + 1,
                "phase": checkpoint["phase"], "state": checkpoint["state"],
                "checkpoint_id": checkpoint["id"]}

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row); result["state"] = json.loads(result["state"]); return result
