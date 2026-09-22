from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .cursors import decode_cursor, encode_cursor


@dataclass(frozen=True)
class Principal:
    id: str
    name: str
    scopes: frozenset[str]


class TokenStore:
    """Persistent, revocable, scoped API tokens. Only SHA-256 token digests are stored."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.execute("PRAGMA busy_timeout=5000")
        with self.lock, self.db:
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS api_tokens (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, digest BLOB NOT NULL UNIQUE,
                    scopes TEXT NOT NULL, created_at TEXT NOT NULL, last_used_at TEXT,
                    expires_at TEXT, revoked_at TEXT, owner_id TEXT, token_kind TEXT NOT NULL DEFAULT 'api'
                );
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
                    password_hash BLOB NOT NULL, password_salt BLOB NOT NULL,
                    created_at TEXT NOT NULL, disabled_at TEXT
                );
            """)
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(api_tokens)")}
            if "owner_id" not in columns:
                self.db.execute("ALTER TABLE api_tokens ADD COLUMN owner_id TEXT")
            if "token_kind" not in columns:
                self.db.execute("ALTER TABLE api_tokens ADD COLUMN token_kind TEXT NOT NULL DEFAULT 'api'")

    @staticmethod
    def digest(token: str) -> bytes:
        return hashlib.sha256(token.encode()).digest()

    def create(self, name: str, scopes: set[str], expires_at: str | None = None, owner_id: str | None = None, token_kind: str = "api") -> tuple[str, str]:
        if not name.strip() or not scopes:
            raise ValueError("token name and at least one scope are required")
        ident, token = secrets.token_hex(12), f"mee_{secrets.token_urlsafe(32)}"
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO api_tokens(id,name,digest,scopes,created_at,expires_at,owner_id,token_kind) VALUES(?,?,?,?,?,?,?,?)",
                (ident, name, self.digest(token), " ".join(sorted(scopes)), now, expires_at, owner_id, token_kind),
            )
        return ident, token

    def authenticate(self, token: str) -> Principal | None:
        digest, now = self.digest(token), datetime.now(timezone.utc).isoformat()
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT * FROM api_tokens WHERE digest=? AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>?)",
                (digest, now),
            ).fetchone()
            if row is None or not hmac.compare_digest(row["digest"], digest):
                return None
            self.db.execute("UPDATE api_tokens SET last_used_at=? WHERE id=?", (now, row["id"]))
            return Principal(row["owner_id"] or row["id"], row["name"], frozenset(row["scopes"].split()))

    def list_metadata(
        self, revoked: bool | None = None, before: str | None = None,
        limit: int = 100, cursor: str | None = None, owner_id: str | None = None, token_kind: str | None = None,
    ) -> tuple[list[dict], str | None]:
        clauses, parameters = [], []
        if owner_id is not None: clauses.append("owner_id=?"); parameters.append(owner_id)
        if token_kind is not None: clauses.append("token_kind=?"); parameters.append(token_kind)
        if revoked is True: clauses.append("revoked_at IS NOT NULL")
        elif revoked is False: clauses.append("revoked_at IS NULL")
        if before is not None: clauses.append("created_at<?"); parameters.append(before)
        if cursor is not None:
            cursor_time, cursor_id = decode_cursor(cursor)
            clauses.append("(created_at<? OR (created_at=? AND id<?))")
            parameters.extend((cursor_time, cursor_time, cursor_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        page_size = min(max(limit, 1), 500); parameters.append(page_size + 1)
        with self.lock:
            rows = self.db.execute(
                f"SELECT id,name,scopes,created_at,last_used_at,expires_at,revoked_at FROM api_tokens {where} ORDER BY created_at DESC,id DESC LIMIT ?",
                tuple(parameters),
            ).fetchall()
        items = [{**dict(row), "scopes": row["scopes"].split()} for row in rows[:page_size]]
        next_cursor = encode_cursor(items[-1]["created_at"], items[-1]["id"]) if len(rows) > page_size else None
        return items, next_cursor


    def revoke(self, ident: str, owner_id: str | None = None) -> bool:
        with self.lock, self.db:
            query = "UPDATE api_tokens SET revoked_at=? WHERE id=? AND revoked_at IS NULL"
            values: tuple = (datetime.now(timezone.utc).isoformat(), ident)
            if owner_id is not None:
                query += " AND owner_id=?"
                values += (owner_id,)
            return bool(self.db.execute(query, values).rowcount)


    @staticmethod
    def _password(password: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600_000)

    def create_account(self, email: str, password: str, display_name: str) -> tuple[dict, str]:
        normalized = email.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise ValueError("enter a valid email address")
        if len(password) < 12 or len(password) > 200:
            raise ValueError("password must be 12-200 characters")
        if not display_name.strip() or len(display_name.strip()) > 120:
            raise ValueError("display name must be 1-120 characters")
        account_id = "acct_" + secrets.token_hex(12)
        salt = secrets.token_bytes(16)
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self.lock, self.db:
                self.db.execute(
                    "INSERT INTO accounts VALUES(?,?,?,?,?,?,NULL)",
                    (account_id, normalized, display_name.strip(), self._password(password, salt), salt, now),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("an account with this email already exists") from exc
        _, token = self.create(display_name.strip(), {"runs:write", "jobs:read", "jobs:write", "companion:read", "companion:write"}, (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(), account_id, "session")
        return {"id": account_id, "email": normalized, "display_name": display_name.strip(), "created_at": now}, token

    def login_account(self, email: str, password: str) -> tuple[dict, str] | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM accounts WHERE email=? AND disabled_at IS NULL", (email.strip().lower(),)).fetchone()
        candidate = self._password(password, row["password_salt"] if row else b"0" * 16)
        expected = row["password_hash"] if row else b"0" * 32
        if row is None or not hmac.compare_digest(candidate, expected):
            return None
        _, token = self.create(row["display_name"], {"runs:write", "jobs:read", "jobs:write", "companion:read", "companion:write"}, (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(), row["id"], "session")
        return {"id": row["id"], "email": row["email"], "display_name": row["display_name"], "created_at": row["created_at"]}, token

    def get_account(self, account_id: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT id,email,display_name,created_at FROM accounts WHERE id=? AND disabled_at IS NULL", (account_id,)).fetchone()
        return dict(row) if row else None


bearer_scheme = HTTPBearer(auto_error=False)
bearer_dependency = Depends(bearer_scheme)


class Authenticator:
    def __init__(self, store: TokenStore, bootstrap_token: str | None = None, oidc_validator=None, session_auth=None):
        self.store, self.bootstrap, self.oidc, self.session_auth = store, bootstrap_token, oidc_validator, session_auth

    def dependency(self, required: str):
        def check(
            request: Request,
            credentials: HTTPAuthorizationCredentials | None = bearer_dependency,
        ) -> Principal:
            principal = None
            if credentials is not None and credentials.scheme.lower() == "bearer":
                supplied = credentials.credentials
                if self.bootstrap and hmac.compare_digest(supplied, self.bootstrap):
                    principal = Principal("bootstrap", "bootstrap", frozenset({"admin", "runs:write", "jobs:read", "jobs:write", "companion:read", "companion:write"}))
                else:
                    principal = self.store.authenticate(supplied)
                    if principal is None and self.oidc is not None:
                        principal = self.oidc.authenticate(supplied)
            if principal is None and self.session_auth is not None:
                principal = self.session_auth(request.cookies.get("meemee_session", ""))
            if principal is None:
                raise HTTPException(status_code=401, detail="missing or invalid bearer token", headers={"WWW-Authenticate": "Bearer"})
            if required not in principal.scopes and "admin" not in principal.scopes:
                raise HTTPException(status_code=403, detail=f"missing scope: {required}")
            request.state.principal = principal
            return principal
        return check
