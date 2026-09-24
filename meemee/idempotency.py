from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class IdempotencyConflict(ValueError):
    pass


class IdempotencyStore:
    """Durable mutation deduplication keyed by principal, route, key and request digest."""

    def __init__(self, path: Path, ttl_hours: int = 24):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl = timedelta(hours=ttl_hours)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS idempotency (
                principal TEXT NOT NULL, route TEXT NOT NULL, key TEXT NOT NULL,
                request_hash TEXT NOT NULL, response TEXT NOT NULL, status INTEGER NOT NULL,
                created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                PRIMARY KEY(principal, route, key)
            )""")

    @staticmethod
    def request_hash(payload: Any) -> str:
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

    def get(self, principal: str, route: str, key: str, payload: Any) -> tuple[int, Any] | None:
        now = datetime.now(timezone.utc).isoformat()
        digest = self.request_hash(payload)
        with self.lock, self.db:
            self.db.execute("DELETE FROM idempotency WHERE expires_at<=?", (now,))
            row = self.db.execute(
                "SELECT * FROM idempotency WHERE principal=? AND route=? AND key=?",
                (principal, route, key),
            ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != digest:
            raise IdempotencyConflict("idempotency key was already used with a different request")
        return row["status"], json.loads(row["response"])

    def put(self, principal: str, route: str, key: str, payload: Any, status: int, response: Any) -> None:
        if not key or len(key) > 200:
            raise ValueError("idempotency key must contain 1-200 characters")
        now = datetime.now(timezone.utc)
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO idempotency VALUES(?,?,?,?,?,?,?,?)",
                (principal, route, key, self.request_hash(payload), json.dumps(response, default=str), status,
                 now.isoformat(), (now + self.ttl).isoformat()),
            )

    def delete_principal(self, principal: str) -> int:
        """Drop stored responses (which can echo job goals) for a principal."""
        with self.lock, self.db:
            return self.db.execute("DELETE FROM idempotency WHERE principal=?", (principal,)).rowcount

