"""The rate-limit middleware must work with any limiter that offers hit/cleanup/limit.

Regression: the PostgreSQL limiter has no ``identity`` method, and the middleware used to
call ``limiter.identity(request)``, so every non-exempt request 500'd in PostgreSQL mode.
"""
from __future__ import annotations

from typing import ClassVar

from fastapi import FastAPI
from fastapi.testclient import TestClient

from meemee.rate_limit import RateLimitMiddleware, SQLiteRateLimiter, request_identity


class CountingLimiter:
    """Same public surface as meemee_persist_pg.rate_limit.PostgreSQLRateLimiter."""

    def __init__(self, limit: int):
        self.limit, self.counts, self.keys = limit, {}, []

    def hit(self, identity: str, now: float | None = None):
        self.keys.append(identity)
        self.counts[identity] = self.counts.get(identity, 0) + 1
        count = self.counts[identity]
        return count <= self.limit, max(self.limit - count, 0), 9_999_999_999

    def cleanup(self, now: float | None = None) -> int:
        return 0


def _app(limiter) -> TestClient:
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, limiter=limiter)

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return TestClient(app)


def test_middleware_works_with_limiter_without_identity_method():
    limiter = CountingLimiter(limit=2)
    client = _app(limiter)
    assert client.get("/ping", headers={"Authorization": "Bearer abc"}).status_code == 200
    assert client.get("/ping", headers={"Authorization": "Bearer abc"}).status_code == 200
    blocked = client.get("/ping", headers={"Authorization": "Bearer abc"})
    assert blocked.status_code == 429 and blocked.headers["RateLimit-Limit"] == "2"
    assert client.get("/ping", headers={"Authorization": "Bearer other"}).status_code == 200
    assert limiter.keys[0].startswith("token:") and "abc" not in limiter.keys[0]


def test_sqlite_identity_matches_shared_function(tmp_path):
    class Request:
        headers: ClassVar[dict[str, str]] = {"authorization": "Bearer xyz"}
        client = None

    assert SQLiteRateLimiter.identity(Request()) == request_identity(Request())
    limiter = SQLiteRateLimiter(tmp_path / "rl.sqlite3", limit=1)
    client = _app(limiter)
    assert client.get("/ping").status_code == 200
    assert client.get("/ping").status_code == 429
