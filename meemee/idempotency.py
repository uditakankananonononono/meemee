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


class IdempotencyInProgress(IdempotencyConflict):
    """Another request with the same key has claimed it and has not stored its response yet."""


PENDING = 0  # status of a claimed key whose response is not stored yet
CLAIM_LEASE = timedelta(minutes=5)  # a crashed claimant frees its key after this
IN_PROGRESS = "a request with this idempotency key is still in progress; retry shortly"


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
        if row["status"] == PENDING:
            raise IdempotencyInProgress(IN_PROGRESS)
        return row["status"], json.loads(row["response"])

    def claim(self, principal: str, route: str, key: str, payload: Any) -> tuple[int, Any] | None:
        """Atomically reserve ``key``: None means this caller owns it and must ``put`` or ``release``.

        Otherwise returns the stored ``(status, response)``, or raises IdempotencyConflict for a
        different payload / IdempotencyInProgress while the first request is still running.
        """
        if not key or len(key) > 200:
            raise ValueError("idempotency key must contain 1-200 characters")
        now = datetime.now(timezone.utc)
        with self.lock, self.db:
            self.db.execute("DELETE FROM idempotency WHERE expires_at<=?", (now.isoformat(),))
            inserted = self.db.execute(
                "INSERT OR IGNORE INTO idempotency VALUES(?,?,?,?,?,?,?,?)",
                (principal, route, key, self.request_hash(payload), "null", PENDING,
                 now.isoformat(), (now + CLAIM_LEASE).isoformat()),
            ).rowcount
        return None if inserted else self.get(principal, route, key, payload)

    def release(self, principal: str, route: str, key: str) -> None:
        """Drop an unfinished claim so a retry can run (the request failed before storing a response)."""
        with self.lock, self.db:
            self.db.execute("DELETE FROM idempotency WHERE principal=? AND route=? AND key=? AND status=?",
                            (principal, route, key, PENDING))

    def put(self, principal: str, route: str, key: str, payload: Any, status: int, response: Any) -> None:
        if not key or len(key) > 200:
            raise ValueError("idempotency key must contain 1-200 characters")
        now = datetime.now(timezone.utc)
        with self.lock, self.db:
            self.db.execute(
                """INSERT INTO idempotency VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(principal, route, key) DO UPDATE SET request_hash=excluded.request_hash,
                   response=excluded.response, status=excluded.status, created_at=excluded.created_at,
                   expires_at=excluded.expires_at WHERE idempotency.status=0""",
                (principal, route, key, self.request_hash(payload), json.dumps(response, default=str), status,
                 now.isoformat(), (now + self.ttl).isoformat()),
            )

    def ping(self) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1").fetchone() is not None

    def delete_principal(self, principal: str) -> int:
        """Drop stored responses (which can echo job goals) for a principal."""
        with self.lock, self.db:
            return self.db.execute("DELETE FROM idempotency WHERE principal=?", (principal,)).rowcount

