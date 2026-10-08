"""PostgreSQL idempotency records: the same contract as ``meemee.idempotency.IdempotencyStore``.

With per-host idempotency.sqlite3, a client retrying ``POST /v1/jobs`` with the same
``Idempotency-Key`` after its first attempt succeeded on another host got a second job. Here every
host sees the stored response. Responses are kept as jsonb for the TTL (24 hours by default).
"""
from __future__ import annotations

import json
from datetime import timedelta
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

    def claim(self, principal: str, route: str, key: str, payload: Any, *, claim_token: str | None = None) -> tuple[int, Any] | None:
        """Atomically reserve ``key`` across every host (primary key + ON CONFLICT DO NOTHING).

        None means this caller owns the key and must ``put`` or ``release``; otherwise the stored
        ``(status, response)`` is returned or IdempotencyConflict / IdempotencyInProgress is raised.
        """
        if not key or len(key) > 200:
            raise ValueError("idempotency key must contain 1-200 characters")
        digest = self.request_hash(payload)
        with self.db.transaction() as c:
            c.execute("DELETE FROM meemee_idempotency WHERE expires_at <= clock_timestamp()")
            inserted = c.execute("""INSERT INTO meemee_idempotency(principal, route, key, request_hash, response, status, created_at, expires_at, claim_token)
                                    VALUES (%s, %s, %s, %s, 'null'::jsonb, %s, clock_timestamp(), clock_timestamp() + %s, %s)
                                    ON CONFLICT (principal, route, key) DO NOTHING""",
                                 (principal, route, key, digest, PENDING, CLAIM_LEASE, claim_token)).rowcount
        return None if inserted else self.get(principal, route, key, payload)

    def release(self, principal: str, route: str, key: str, *, claim_token: str | None = None) -> None:
        with self.db.transaction() as c:
            c.execute("DELETE FROM meemee_idempotency WHERE principal=%s AND route=%s AND key=%s AND status=%s AND claim_token IS NOT DISTINCT FROM %s",
                      (principal, route, key, PENDING, claim_token))

    def put(self, principal: str, route: str, key: str, payload: Any, status: int, response: Any, *, claim_token: str | None = None) -> None:
        if type(status) is not int or not 100 <= status <= 599:
            raise ValueError("response status must be an HTTP status integer (100-599)")
        if not key or len(key) > 200:
            raise ValueError("idempotency key must contain 1-200 characters")
        encoded_response = json.dumps(response, default=str, allow_nan=False)
        digest = self.request_hash(payload)
        with self.db.transaction() as c:
            # Serialize owner verification with replacement/release/publication.
            reserved = c.execute("SELECT request_hash, status, claim_token, expires_at FROM meemee_idempotency WHERE principal=%s AND route=%s AND key=%s FOR UPDATE",
                                 (principal, route, key)).fetchone()
            server_now = c.execute("SELECT clock_timestamp() AS now").fetchone()["now"]
            protected = claim_token is not None or (reserved is not None and reserved["claim_token"] is not None)
            if protected and (reserved is None or reserved["claim_token"] != claim_token
                              or reserved["status"] != PENDING or reserved["expires_at"] <= server_now):
                raise IdempotencyConflict("idempotency claim ownership was lost")
            # ON CONFLICT locks the existing row even when its WHERE rejects the update.
            # Check the digest while that lock is held, including completed-key replays.
            published = c.execute("""INSERT INTO meemee_idempotency(principal, route, key, request_hash, response, status, created_at, expires_at)
                         VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                         ON CONFLICT (principal, route, key) DO UPDATE SET
                           response=EXCLUDED.response, status=EXCLUDED.status, created_at=EXCLUDED.created_at,
                           expires_at=EXCLUDED.expires_at WHERE meemee_idempotency.status = 0
                           AND meemee_idempotency.request_hash=EXCLUDED.request_hash
                           AND meemee_idempotency.claim_token IS NOT DISTINCT FROM %s
                         RETURNING request_hash""",
                      (principal, route, key, digest, Jsonb(json.loads(encoded_response)),
                       status, server_now, server_now + self.ttl, claim_token)).fetchone()
            if published is None:
                reserved = c.execute(
                    "SELECT request_hash, claim_token FROM meemee_idempotency WHERE principal=%s AND route=%s AND key=%s",
                    (principal, route, key),
                ).fetchone()
                if reserved is not None and reserved["claim_token"] is not None:
                    raise IdempotencyConflict("idempotency claim ownership was lost")
                if reserved is not None and reserved["request_hash"] != digest:
                    raise IdempotencyConflict("idempotency key was already used with a different request")

    def delete_principal(self, principal: str) -> int:
        with self.db.transaction() as c:
            return c.execute("DELETE FROM meemee_idempotency WHERE principal=%s", (principal,)).rowcount

    def ping(self) -> bool:
        with self.db.transaction() as c:
            c.execute("SELECT 1 FROM meemee_idempotency LIMIT 0")
        return True
