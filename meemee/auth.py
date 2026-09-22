from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


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
                    expires_at TEXT, revoked_at TEXT
                );
            """)

    @staticmethod
    def digest(token: str) -> bytes:
        return hashlib.sha256(token.encode()).digest()

    def create(self, name: str, scopes: set[str], expires_at: str | None = None) -> tuple[str, str]:
        if not name.strip() or not scopes:
            raise ValueError("token name and at least one scope are required")
        ident, token = secrets.token_hex(12), f"mee_{secrets.token_urlsafe(32)}"
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO api_tokens(id,name,digest,scopes,created_at,expires_at) VALUES(?,?,?,?,?,?)",
                (ident, name, self.digest(token), " ".join(sorted(scopes)), now, expires_at),
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
            return Principal(row["id"], row["name"], frozenset(row["scopes"].split()))

    def list_metadata(
        self, revoked: bool | None = None, before: str | None = None, limit: int = 100
    ) -> list[dict]:
        clauses, parameters = [], []
        if revoked is True: clauses.append("revoked_at IS NOT NULL")
        elif revoked is False: clauses.append("revoked_at IS NULL")
        if before is not None:
            clauses.append("created_at<?"); parameters.append(before)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        parameters.append(min(max(limit, 1), 500))
        with self.lock:
            rows = self.db.execute(
                f"SELECT id,name,scopes,created_at,last_used_at,expires_at,revoked_at FROM api_tokens {where} ORDER BY created_at DESC,id DESC LIMIT ?",
                tuple(parameters),
            ).fetchall()
        return [{**dict(row), "scopes": row["scopes"].split()} for row in rows]

    def revoke(self, ident: str) -> bool:
        with self.lock, self.db:
            return bool(self.db.execute(
                "UPDATE api_tokens SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                (datetime.now(timezone.utc).isoformat(), ident),
            ).rowcount)


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
                    principal = Principal("bootstrap", "bootstrap", frozenset({"admin", "runs:write", "jobs:read", "jobs:write"}))
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
