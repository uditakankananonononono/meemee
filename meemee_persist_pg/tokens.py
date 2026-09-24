"""PostgreSQL API tokens and customer accounts: the same contract as ``meemee.auth.TokenStore``.

In PostgreSQL mode every API host authenticates against these rows, so a token minted, revoked
or rotated on one host is honored the same way on every other host. Only SHA-256 digests of
secrets are stored. Timestamps come back as ISO 8601 strings, like the SQLite store.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from psycopg.errors import UniqueViolation

from ._db import Database

SESSION_SCOPES = ("companion:read", "companion:write", "jobs:read", "jobs:write", "runs:write")
_TOKEN_COLUMNS = "id,name,digest,scopes,created_at,last_used_at,expires_at,revoked_at,owner_id,token_kind"


def _iso(value: Any) -> Any:
    # UTC regardless of the server/session TimeZone, matching the SQLite store's stored strings.
    return value.astimezone(timezone.utc).isoformat() if isinstance(value, datetime) else value


def _expiry(value: str | None) -> datetime | None:
    """Parse an ISO 8601 expiry (naive = UTC). Rejects anything else instead of guessing."""
    if value is None:
        return None
    text = str(value).strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("expires_at must be an ISO 8601 timestamp") from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _principal(row):
    from meemee.auth import Principal
    return Principal(row["owner_id"] or row["id"], row["name"], frozenset(row["scopes"]))


class TokenStore:
    """Persistent, revocable, scoped API tokens plus accounts, shared by every host on the database."""

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def digest(token: str) -> bytes:
        return hashlib.sha256(token.encode()).digest()

    @staticmethod
    def _password(password: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)

    def ping(self) -> bool:
        with self.db.transaction() as c:
            return c.execute("SELECT 1 AS ok").fetchone()["ok"] == 1

    # ----- tokens -------------------------------------------------------------------------
    def _insert_token(self, c, name: str, scopes, expires_at: datetime | None, owner_id: str | None, token_kind: str) -> tuple[str, str]:
        ident, token = secrets.token_hex(12), f"mee_{secrets.token_urlsafe(32)}"
        c.execute(
            "INSERT INTO meemee_api_tokens(id,name,digest,scopes,created_at,expires_at,owner_id,token_kind) "
            "VALUES(%s,%s,%s,%s,clock_timestamp(),%s,%s,%s)",
            (ident, name, self.digest(token), sorted(scopes), expires_at, owner_id, token_kind),
        )
        return ident, token

    def create(self, name: str, scopes: set[str], expires_at: str | None = None, owner_id: str | None = None,
               token_kind: str = "api") -> tuple[str, str]:
        if not name.strip() or not scopes:
            raise ValueError("token name and at least one scope are required")
        expiry = _expiry(expires_at)
        with self.db.transaction() as c:
            return self._insert_token(c, name, scopes, expiry, owner_id, token_kind)

    def authenticate(self, token: str):
        digest = self.digest(token)
        with self.db.transaction() as c:
            row = c.execute(
                "SELECT id,name,digest,scopes,owner_id FROM meemee_api_tokens WHERE digest=%s AND revoked_at IS NULL "
                "AND (expires_at IS NULL OR expires_at>clock_timestamp()) FOR UPDATE", (digest,)).fetchone()
            if not row or not hmac.compare_digest(bytes(row["digest"]), digest):
                return None
            c.execute("UPDATE meemee_api_tokens SET last_used_at=clock_timestamp() WHERE id=%s", (row["id"],))
        return _principal(row)

    def introspect(self, token: str) -> dict | None:
        """Redacted metadata and derived state for a raw token; never updates last_used_at."""
        digest = self.digest(token)
        with self.db.transaction() as c:
            row = c.execute(f"SELECT {_TOKEN_COLUMNS}, clock_timestamp() AS now FROM meemee_api_tokens WHERE digest=%s",
                            (digest,)).fetchone()
        if not row or not hmac.compare_digest(bytes(row["digest"]), digest):
            return None
        from meemee.auth import token_introspection
        record = {key: (_iso(value) if key != "now" else value) for key, value in row.items()}
        return token_introspection(record, row["now"])

    def list_metadata(self, revoked: bool | None = None, before: str | None = None, limit: int = 100,
                      cursor: str | None = None, owner_id: str | None = None,
                      token_kind: str | None = None) -> tuple[list[dict], str | None]:
        from meemee.cursors import decode_cursor, encode_cursor

        clauses, parameters = [], []
        if owner_id is not None:
            clauses.append("owner_id=%s"); parameters.append(owner_id)
        if token_kind is not None:
            clauses.append("token_kind=%s"); parameters.append(token_kind)
        if revoked is True:
            clauses.append("revoked_at IS NOT NULL")
        elif revoked is False:
            clauses.append("revoked_at IS NULL")
        if before is not None:
            clauses.append("created_at<%s"); parameters.append(_expiry(before))
        if cursor is not None:
            cursor_time, cursor_id = decode_cursor(cursor)
            moment = _expiry(cursor_time)
            clauses.append("(created_at<%s OR (created_at=%s AND id<%s))")
            parameters.extend((moment, moment, cursor_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        page_size = min(max(limit, 1), 500)
        parameters.append(page_size + 1)
        with self.db.transaction() as c:
            rows = c.execute(
                f"SELECT id,name,scopes,created_at,last_used_at,expires_at,revoked_at FROM meemee_api_tokens {where} "
                "ORDER BY created_at DESC,id DESC LIMIT %s", tuple(parameters)).fetchall()
        items = [{key: (sorted(value) if key == "scopes" else _iso(value)) for key, value in row.items()} for row in rows[:page_size]]
        next_cursor = encode_cursor(items[-1]["created_at"], items[-1]["id"]) if len(rows) > page_size else None
        return items, next_cursor

    def revoke(self, ident: str, owner_id: str | None = None) -> bool:
        query = "UPDATE meemee_api_tokens SET revoked_at=clock_timestamp() WHERE id=%s AND revoked_at IS NULL"
        values: tuple = (ident,)
        if owner_id is not None:
            query += " AND owner_id=%s"
            values += (owner_id,)
        with self.db.transaction() as c:
            return bool(c.execute(query, values).rowcount)

    # ----- accounts -----------------------------------------------------------------------
    @staticmethod
    def _account(row) -> dict:
        return {"id": row["id"], "email": row["email"], "display_name": row["display_name"], "created_at": _iso(row["created_at"])}

    def _session(self, c, display_name: str, account_id: str) -> str:
        return self._insert_token(c, display_name, SESSION_SCOPES, datetime.now(timezone.utc) + timedelta(days=30),
                                  account_id, "session")[1]

    def create_account(self, email: str, password: str, display_name: str) -> tuple[dict, str]:
        normalized = email.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise ValueError("enter a valid email address")
        if len(password) < 12 or len(password) > 200:
            raise ValueError("password must be 12-200 characters")
        if not display_name.strip() or len(display_name.strip()) > 120:
            raise ValueError("display name must be 1-120 characters")
        account_id, salt = "acct_" + secrets.token_hex(12), secrets.token_bytes(16)
        try:
            with self.db.transaction() as c:
                row = c.execute(
                    "INSERT INTO meemee_accounts(id,email,display_name,password_hash,password_salt,created_at) "
                    "VALUES(%s,%s,%s,%s,%s,clock_timestamp()) RETURNING id,email,display_name,created_at",
                    (account_id, normalized, display_name.strip(), self._password(password, salt), salt)).fetchone()
                token = self._session(c, display_name.strip(), account_id)
        except UniqueViolation as exc:
            raise ValueError("an account with this email already exists") from exc
        return self._account(row), token

    def login_account(self, email: str, password: str) -> tuple[dict, str] | None:
        now = datetime.now(timezone.utc)
        with self.db.transaction() as c:
            row = c.execute("SELECT * FROM meemee_accounts WHERE email=%s AND disabled_at IS NULL FOR UPDATE",
                            (email.strip().lower(),)).fetchone()
            candidate = self._password(password, bytes(row["password_salt"]) if row else b"0" * 16)
            expected = bytes(row["password_hash"]) if row else b"0" * 32
            if row is not None and row["locked_until"] and row["locked_until"] > now:
                return None
            if row is None or not hmac.compare_digest(candidate, expected):
                if row is not None:
                    failures = int(row["failed_logins"]) + 1
                    locked = now + timedelta(minutes=15) if failures >= 5 else None
                    c.execute("UPDATE meemee_accounts SET failed_logins=%s,locked_until=%s WHERE id=%s", (failures, locked, row["id"]))
                return None
            c.execute("UPDATE meemee_accounts SET failed_logins=0,locked_until=NULL WHERE id=%s", (row["id"],))
            token = self._session(c, row["display_name"], row["id"])
        return self._account(row), token

    def account_by_email(self, email: str) -> dict | None:
        with self.db.transaction() as c:
            row = c.execute("SELECT id,email,display_name,created_at FROM meemee_accounts WHERE email=%s AND disabled_at IS NULL",
                            (email.strip().lower(),)).fetchone()
        return self._account(row) if row else None

    def get_account(self, account_id: str) -> dict | None:
        with self.db.transaction() as c:
            row = c.execute("SELECT id,email,display_name,created_at FROM meemee_accounts WHERE id=%s AND disabled_at IS NULL",
                            (account_id,)).fetchone()
        return self._account(row) if row else None

    def reset_password(self, account_id: str, password: str) -> bool:
        if len(password) < 12 or len(password) > 200:
            raise ValueError("password must be 12-200 characters")
        salt = secrets.token_bytes(16)
        with self.db.transaction() as c:
            changed = c.execute(
                "UPDATE meemee_accounts SET password_hash=%s,password_salt=%s,failed_logins=0,locked_until=NULL "
                "WHERE id=%s AND disabled_at IS NULL", (self._password(password, salt), salt, account_id)).rowcount
            if changed:
                c.execute("UPDATE meemee_api_tokens SET revoked_at=clock_timestamp() WHERE owner_id=%s AND revoked_at IS NULL", (account_id,))
        return bool(changed)

    def disable_account(self, account_id: str) -> bool:
        with self.db.transaction() as c:
            changed = c.execute("UPDATE meemee_accounts SET disabled_at=clock_timestamp() WHERE id=%s AND disabled_at IS NULL",
                                (account_id,)).rowcount
            if changed:
                c.execute("UPDATE meemee_api_tokens SET revoked_at=clock_timestamp() WHERE owner_id=%s AND revoked_at IS NULL", (account_id,))
        return bool(changed)
