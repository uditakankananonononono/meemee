"""PostgreSQL idempotency records: the same contract as ``meemee.idempotency.IdempotencyStore``.

With per-host idempotency.sqlite3, a client retrying ``POST /v1/jobs`` with the same
``Idempotency-Key`` after its first attempt succeeded on another host got a second job. Here every
host sees the stored response. Responses are kept as jsonb for the TTL (24 hours by default).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg.types.json import Jsonb

from meemee.idempotency import (
    CLAIM_LEASE,
    IN_PROGRESS,
    PENDING,
    IdempotencyConflict,
    IdempotencyInProgress,
)
from meemee.idempotency import IdempotencyStore as _SQLiteStore

from ._db import Database


class IdempotencyStore:
    """Durable mutation deduplication keyed by principal, route, key and request digest (PostgreSQL)."""

    request_hash = staticmethod(_SQLiteStore.request_hash)

    def __init__(self, db: Database, ttl_hours: int = 24):
        self.db, self.ttl = db, timedelta(hours=ttl_hours)

    def get(self, principal: str, route: str, key: str, payload: Any) -> tuple[int, Any] | None:
        digest = self.request_hash(payload)
        with self.db.transaction() as c:
            c.execute("DELETE FROM meemee_idempotency WHERE expires_at <= clock_timestamp()")
            row = c.execute("SELECT request_hash, status, response FROM meemee_idempotency WHERE principal=%s AND route=%s AND key=%s",
                            (principal, route, key)).fetchone()
        if row is None:
            return None
        if row["request_hash"] != digest:
            raise IdempotencyConflict("idempotency key was already used with a different request")
        if row["status"] == PENDING:
            raise IdempotencyInProgress(IN_PROGRESS)
        return row["status"], row["response"]

    def claim(self, principal: str, route: str, key: str, payload: Any) -> tuple[int, Any] | None:
        """Atomically reserve ``key`` across every host (primary key + ON CONFLICT DO NOTHING).

        None means this caller owns the key and must ``put`` or ``release``; otherwise the stored
        ``(status, response)`` is returned or IdempotencyConflict / IdempotencyInProgress is raised.
        """
        if not key or len(key) > 200:
            raise ValueError("idempotency key must contain 1-200 characters")
        with self.db.transaction() as c:
            c.execute("DELETE FROM meemee_idempotency WHERE expires_at <= clock_timestamp()")
            inserted = c.execute("""INSERT INTO meemee_idempotency(principal, route, key, request_hash, response, status, created_at, expires_at)
                                    VALUES (%s, %s, %s, %s, 'null'::jsonb, %s, clock_timestamp(), clock_timestamp() + %s)
                                    ON CONFLICT (principal, route, key) DO NOTHING""",
                                 (principal, route, key, self.request_hash(payload), PENDING, CLAIM_LEASE)).rowcount
        return None if inserted else self.get(principal, route, key, payload)

    def release(self, principal: str, route: str, key: str) -> None:
        with self.db.transaction() as c:
            c.execute("DELETE FROM meemee_idempotency WHERE principal=%s AND route=%s AND key=%s AND status=%s",
                      (principal, route, key, PENDING))

    def put(self, principal: str, route: str, key: str, payload: Any, status: int, response: Any) -> None:
        if not key or len(key) > 200:
            raise ValueError("idempotency key must contain 1-200 characters")
        now = datetime.now(timezone.utc)
        with self.db.transaction() as c:
            c.execute("""INSERT INTO meemee_idempotency(principal, route, key, request_hash, response, status, created_at, expires_at)
                         VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                         ON CONFLICT (principal, route, key) DO UPDATE SET request_hash=EXCLUDED.request_hash,
                           response=EXCLUDED.response, status=EXCLUDED.status, created_at=EXCLUDED.created_at,
                           expires_at=EXCLUDED.expires_at WHERE meemee_idempotency.status = 0""",
                      (principal, route, key, self.request_hash(payload), Jsonb(json.loads(json.dumps(response, default=str))),
                       status, now, now + self.ttl))

    def delete_principal(self, principal: str) -> int:
        with self.db.transaction() as c:
            return c.execute("DELETE FROM meemee_idempotency WHERE principal=%s", (principal,)).rowcount

    def ping(self) -> bool:
        with self.db.transaction() as c:
            c.execute("SELECT 1 FROM meemee_idempotency LIMIT 0")
        return True
