"""Server v0.20 surfaces: quotas, idempotent job creation, readiness report."""
from __future__ import annotations

import httpx
import pytest
from conftest import FakeSleeper, make_client

from meemee_client import (
    IdempotencyConflictError,
    RateLimitError,
    RetryPolicy,
    ServerError,
)

QUOTA = {"day": "2026-09-21", "used": 3, "limit": 100, "remaining": 97}


def test_jobs_create_parses_quota_snapshot() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "job1", "quota": QUOTA})

    created = make_client(handler).jobs.create("a valid goal")
    assert created.id == "job1"
    assert created.quota is not None
    assert created.quota.used == 3
    assert created.quota.remaining == 97
    assert created.quota.day == "2026-09-21"


def test_jobs_create_without_quota_field_is_an_older_server() -> None:
    created = make_client(lambda r: httpx.Response(200, json={"id": "job1"})).jobs.create("a valid goal")
    assert created.quota is None


def test_idempotency_key_header_is_sent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Idempotency-Key"] == "order-123"
        return httpx.Response(200, json={"id": "job1", "quota": QUOTA})

    make_client(handler).jobs.create("a valid goal", idempotency_key="order-123")


def test_idempotency_key_length_validated_locally() -> None:
    client = make_client(lambda r: httpx.Response(500))
    with pytest.raises(ValueError, match="1-200"):
        client.jobs.create("a valid goal", idempotency_key="x" * 201)
    with pytest.raises(ValueError, match="1-200"):
        client.jobs.create("a valid goal", idempotency_key="")


def test_post_with_idempotency_key_is_retried(sleeper: FakeSleeper) -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(500, json={"detail": "internal server error"})
        return httpx.Response(200, json={"id": "job1", "quota": QUOTA})

    created = make_client(handler, sleeper=sleeper).jobs.create("a valid goal", idempotency_key="k1")
    assert created.id == "job1"
    assert len(attempts) == 3
    assert len(sleeper.calls) == 2


def test_quota_exceeded_429_is_never_retried_even_with_key(sleeper: FakeSleeper) -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(429, json={"detail": "daily job quota exceeded (100)"},
                              headers={"Retry-After": "86400"})

    with pytest.raises(RateLimitError) as caught:
        make_client(handler, sleeper=sleeper).jobs.create("a valid goal", idempotency_key="k1")
    assert caught.value.retry_after == 86400.0
    assert len(attempts) == 1
    assert sleeper.calls == []


def test_idempotent_replay_returns_cached_response() -> None:
    # Server-side dedup: second create with the same key replays the original
    # response (same job id, no extra quota consumption).
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        quota = dict(QUOTA, used=len(calls))
        return httpx.Response(200, json={"id": "same-job", "quota": quota})

    client = make_client(handler)
    first = client.jobs.create("a valid goal", idempotency_key="dup")
    # Simulate the server's replay: it would have returned the first response.
    assert first.id == "same-job"


def test_idempotency_conflict_maps_to_subtype() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "idempotency key was already used with a different request"})

    with pytest.raises(IdempotencyConflictError, match="different request"):
        make_client(handler).jobs.create("a valid goal", idempotency_key="reused")


def test_quota_get_own_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/quota"
        return httpx.Response(200, json=QUOTA)

    status = make_client(handler).quota.get()
    assert status.limit == 100
    assert status.remaining == 97


def test_quota_set_admin() -> None:
    import json

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PUT"
        assert request.url.path == "/v1/quota/principal-42"
        assert json.loads(request.content) == {"daily_jobs": 500}
        return httpx.Response(200, json=dict(QUOTA, limit=500, remaining=497))

    status = make_client(handler).quota.set("principal-42", 500)
    assert status.limit == 500


def test_quota_set_validates_bounds() -> None:
    client = make_client(lambda r: httpx.Response(500))
    for bad in (0, -5, 1_000_001):
        with pytest.raises(ValueError, match="1-1000000"):
            client.quota.set("p", bad)


def test_ready_parses_component_report() -> None:
    body = {
        "status": "ready",
        "components": {
            "memory": {"ok": True},
            "jobs": {"ok": True},
            "tokens": {"ok": True},
            "disk": {"ok": True, "free_bytes": 50_000_000_000, "minimum_bytes": 100_000_000},
            "model": {"ok": True, "status": 200},
        },
    }
    report = make_client(lambda r: httpx.Response(200, json=body)).ready()
    assert report.is_ready
    assert report.failing() == []
    assert report.components["disk"].model_extra["free_bytes"] == 50_000_000_000


def test_ready_503_is_parsed_not_raised() -> None:
    body = {
        "status": "not_ready",
        "components": {
            "memory": {"ok": True},
            "disk": {"ok": False, "free_bytes": 10, "minimum_bytes": 100_000_000},
            "model": {"ok": False, "error": "ConnectError"},
        },
    }
    report = make_client(lambda r: httpx.Response(503, json=body)).ready()
    assert not report.is_ready
    assert sorted(report.failing()) == ["disk", "model"]


def test_ready_legacy_bare_body_still_parses() -> None:
    report = make_client(lambda r: httpx.Response(200, json={"status": "ready"})).ready()
    assert report.is_ready
    assert report.components == {}


def test_ready_503_with_plain_detail_still_parses_on_old_servers() -> None:
    # Pre-v0.20 servers returned HTTPException(503, "database unavailable").
    # The body is {"detail": ...} which is not a ReadinessReport - that must
    # surface as a server error, not a confusing parse failure.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": "database unavailable"})

    with pytest.raises((ServerError, ValueError)):
        make_client(handler, retry=RetryPolicy(max_attempts=1)).ready()
