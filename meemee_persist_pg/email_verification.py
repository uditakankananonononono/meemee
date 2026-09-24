"""PostgreSQL email-verification and password-reset challenges.

Same contract as ``meemee.email_verification.EmailVerificationStore``. In PostgreSQL mode every API
host reads and writes these rows, so a verification or reset link issued by one host works on
whichever host the load balancer sends the click to. Only SHA-256 digests of the raw tokens are
stored; each challenge is one-time, expiring, and re-issuing replaces the previous one. The tables
come from migration 003 and cascade-delete with their account.
"""
from __future__ import annotations

import hashlib
import secrets

from ._db import Database

_TABLES = {"verify": ("meemee_email_verifications", "verified_at"), "reset": ("meemee_password_resets", "used_at")}


def _digest(raw: str) -> bytes:
    return hashlib.sha256(raw.encode()).digest()


class EmailVerificationStore:
    """One-time, expiring email verification and password-reset challenges (PostgreSQL)."""

    def __init__(self, db: Database):
        self.db = db

    def _issue(self, kind: str, account_id: str, ttl_minutes: int) -> str:
        table, used = _TABLES[kind]
        raw = secrets.token_urlsafe(32)
        with self.db.transaction() as c:
            c.execute(
                f"""INSERT INTO {table}(account_id, token_digest, expires_at, sent_at)
                    VALUES (%s, %s, clock_timestamp() + (%s * interval '1 minute'), clock_timestamp())
                    ON CONFLICT (account_id) DO UPDATE SET token_digest = excluded.token_digest,
                      expires_at = excluded.expires_at, sent_at = excluded.sent_at, {used} = NULL""",
                (account_id, _digest(raw), ttl_minutes),
            )
        return raw

    def _consume(self, kind: str, account_id: str, raw: str) -> bool:
        table, used = _TABLES[kind]
        with self.db.transaction() as c:
            # Row lock: two hosts receiving the same link at once cannot both consume it.
            row = c.execute(
                f"SELECT token_digest, expires_at > clock_timestamp() AS live, {used} AS used FROM {table} WHERE account_id=%s FOR UPDATE",
                (account_id,),
            ).fetchone()
            if row is None or row["used"] is not None or not row["live"]:
                return False
            if not secrets.compare_digest(bytes(row["token_digest"]), _digest(raw)):
                return False
            c.execute(f"UPDATE {table} SET {used} = clock_timestamp() WHERE account_id=%s", (account_id,))
        return True

    def issue(self, account_id: str, ttl_minutes: int = 30) -> str:
        return self._issue("verify", account_id, ttl_minutes)

    def verify(self, account_id: str, raw: str) -> bool:
        return self._consume("verify", account_id, raw)

    def status(self, account_id: str) -> bool:
        with self.db.transaction() as c:
            row = c.execute("SELECT verified_at FROM meemee_email_verifications WHERE account_id=%s", (account_id,)).fetchone()
        return bool(row and row["verified_at"])

    def issue_password_reset(self, account_id: str, ttl_minutes: int = 20) -> str:
        return self._issue("reset", account_id, ttl_minutes)

    def consume_password_reset(self, account_id: str, raw: str) -> bool:
        return self._consume("reset", account_id, raw)

    def ping(self) -> bool:
        with self.db.transaction() as c:
            c.execute("SELECT 1 FROM meemee_email_verifications LIMIT 0")
            return True
