from __future__ import annotations

import hashlib
import math
import sqlite3
import threading
import time
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


def request_identity(request) -> str:
    """Rate-limit key: a hash of the bearer token, else the client IP. Backend-independent."""
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return "token:" + hashlib.sha256(authorization[7:].encode()).hexdigest()[:24]
    return "ip:" + (request.client.host if request.client else "unknown")


class SQLiteRateLimiter:
    """Atomic fixed-window limiter shared by all processes on one host."""

    def __init__(self, path: Path, limit: int = 60, window_seconds: int = 60):
        if limit < 1 or window_seconds < 1:
            raise ValueError("rate limit and window must be positive")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.limit, self.window = limit, window_seconds
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS rate_limits (
                identity TEXT NOT NULL, window_start INTEGER NOT NULL, count INTEGER NOT NULL,
                PRIMARY KEY(identity, window_start)
            );
        """)

    def hit(self, identity: str, now: float | None = None) -> tuple[bool, int, int]:
        timestamp = int(time.time() if now is None else now)
        window_start = timestamp - timestamp % self.window
        reset = window_start + self.window
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute(
                "INSERT INTO rate_limits(identity,window_start,count) VALUES(?,?,1) ON CONFLICT(identity,window_start) DO UPDATE SET count=count+1",
                (identity, window_start),
            )
            count = self.db.execute(
                "SELECT count FROM rate_limits WHERE identity=? AND window_start=?",
                (identity, window_start),
            ).fetchone()[0]
            self.db.execute("COMMIT")
        return count <= self.limit, max(self.limit - count, 0), reset

    def cleanup(self, now: float | None = None) -> int:
        cutoff = int(time.time() if now is None else now) - self.window * 2
        with self.lock, self.db:
            return self.db.execute("DELETE FROM rate_limits WHERE window_start<?", (cutoff,)).rowcount

    @staticmethod
    def identity(request) -> str:
        return request_identity(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, limiter: SQLiteRateLimiter, exempt_paths: set[str] | None = None):
        super().__init__(app)
        self.limiter = limiter
        self.exempt = exempt_paths or {"/health", "/ready"}
        self.requests = 0

    async def dispatch(self, request, call_next):
        if request.url.path in self.exempt:
            return await call_next(request)
        self.requests += 1
        if self.requests % 1000 == 0:
            self.limiter.cleanup()
        # Every limiter backend (SQLite, PostgreSQL) only needs hit/cleanup/limit; the key is shared.
        allowed, remaining, reset = self.limiter.hit(request_identity(request))
        headers = {
            "RateLimit-Limit": str(self.limiter.limit),
            "RateLimit-Remaining": str(remaining),
            "RateLimit-Reset": str(reset),
        }
        if not allowed:
            retry = max(reset - math.floor(time.time()), 1)
            return JSONResponse(
                {"detail": "rate limit exceeded"}, 429,
                headers={**headers, "Retry-After": str(retry)},
            )
        response = await call_next(request)
        response.headers.update(headers)
        return response
