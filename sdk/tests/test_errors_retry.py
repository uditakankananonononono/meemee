"""Error mapping, retry semantics, and rate-limit metadata."""
from __future__ import annotations

import httpx
import pytest
from conftest import JOB_ID, FakeSleeper, make_client

from meemee_client import (
    AuthenticationError,
    BadRequestError,
    ConflictError,
    NetworkError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    RetryPolicy,
    ServerError,
    ValidationError,
)


def test_401_maps_to_authentication_error_with_challenge() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "missing or invalid bearer token"},
                              headers={"WWW-Authenticate": "Bearer", "X-Request-ID": "rid1"})

    with pytest.raises(AuthenticationError) as caught:
        make_client(handler).jobs.get(JOB_ID)
    error = caught.value
    assert error.www_authenticate == "Bearer"
    assert error.request_id == "rid1"
    assert "request_id=rid1" in str(error)


def test_403_extracts_missing_scope() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "missing scope: jobs:write"})

    with pytest.raises(PermissionDeniedError) as caught:
        make_client(handler).jobs.create("some goal")
    assert caught.value.missing_scope == "jobs:write"


def test_404_maps_to_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "job not found"})

    with pytest.raises(NotFoundError, match="job not found"):
        make_client(handler).jobs.get("nope")


def test_409_maps_to_conflict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "cannot cancel job in done state"})

    with pytest.raises(ConflictError, match="done state"):
        make_client(handler).jobs.cancel(JOB_ID)


def test_422_fastapi_issue_list_maps_to_validation_error() -> None:
    detail = [{"loc": ["body", "goal"], "msg": "ensure this value has at least 2 characters", "type": "value_error"}]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": detail})

    with pytest.raises(ValidationError) as caught:
        make_client(handler).jobs.create("x" * 5)  # passes local check, fails "server-side"
    assert caught.value.issues == detail
    assert "body.goal" in str(caught.value)


def test_422_string_detail_maps_without_issues() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "run_at must be ISO 8601"})

    with pytest.raises(ValidationError) as caught:
        make_client(handler).jobs.create("a goal", run_at="2026-09-22T06:00:00Z")
    assert caught.value.issues is None
    assert caught.value.detail == "run_at must be ISO 8601"


def test_400_maps_to_bad_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"detail": "Last-Event-ID must be an integer"})

    with pytest.raises(BadRequestError, match="Last-Event-ID"):
        make_client(handler).jobs.events(JOB_ID)


def test_500_uses_body_request_id_when_header_missing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "internal server error", "request_id": "body-rid"})

    with pytest.raises(ServerError) as caught:
        make_client(handler, retry=RetryPolicy(max_attempts=1)).health()
    assert caught.value.request_id == "body-rid"


def test_502_run_failure_maps_to_server_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"detail": "agent run failed: model endpoint down"})

    with pytest.raises(ServerError, match="model endpoint down"):
        make_client(handler).runs.create("a valid goal")


def test_non_json_error_body_still_maps() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="proxy exploded", headers={"Content-Type": "text/plain"})

    with pytest.raises(ServerError, match="proxy exploded"):
        make_client(handler, retry=RetryPolicy(max_attempts=1)).health()


def test_429_raises_rate_limit_error_with_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "rate limit exceeded"},
                              headers={"Retry-After": "17"})

    with pytest.raises(RateLimitError) as caught:
        make_client(handler, retry=RetryPolicy(max_attempts=1)).jobs.get(JOB_ID)
    assert caught.value.retry_after == 17.0


def test_retry_after_is_honoured_before_retrying(sleeper: FakeSleeper) -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(429, json={"detail": "rate limit exceeded"}, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"status": "ok", "version": "0.16.0"})

    make_client(handler, sleeper=sleeper).health()
    assert sleeper.calls == [7.0]
    assert len(attempts) == 2


def test_retry_after_capped_by_policy(sleeper: FakeSleeper) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "rate limit exceeded"}, headers={"Retry-After": "9999"})

    policy = RetryPolicy(max_attempts=2, retry_after_max_seconds=30)
    with pytest.raises(RateLimitError):
        make_client(handler, sleeper=sleeper, retry=policy).health()
    assert sleeper.calls == [30.0]


def test_5xx_retried_with_jittered_backoff_then_succeeds(sleeper: FakeSleeper) -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            return httpx.Response(503, json={"detail": "database unavailable"})
        return httpx.Response(200, json={"status": "ready"})

    policy = RetryPolicy(max_attempts=3, backoff_base_seconds=0.5, backoff_multiplier=2.0)
    assert make_client(handler, sleeper=sleeper, retry=policy).ready().status == "ready"
    assert len(attempts) == 3
    assert len(sleeper.calls) == 2
    assert 0.0 <= sleeper.calls[0] <= 0.5
    assert 0.0 <= sleeper.calls[1] <= 1.0


def test_post_is_not_retried_because_runs_are_not_idempotent() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(500, json={"detail": "internal server error"})

    with pytest.raises(ServerError):
        make_client(handler).jobs.create("a valid goal")
    assert len(attempts) == 1


def test_permanent_errors_fail_fast_without_retry(sleeper: FakeSleeper) -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(403, json={"detail": "missing scope: admin"})

    with pytest.raises(PermissionDeniedError):
        make_client(handler, sleeper=sleeper).audit.list()
    assert len(attempts) == 1
    assert sleeper.calls == []


def test_network_error_retried_on_get(sleeper: FakeSleeper) -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 2:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={"status": "ok", "version": "0.16.0"})

    assert make_client(handler, sleeper=sleeper).health().status == "ok"
    assert len(attempts) == 2


def test_network_error_exhausts_attempts(sleeper: FakeSleeper) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("no route", request=request)

    policy = RetryPolicy(max_attempts=2)
    with pytest.raises(NetworkError, match="no route"):
        make_client(handler, sleeper=sleeper, retry=policy).health()


def test_network_error_on_post_not_retried() -> None:
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        raise httpx.WriteError("broken pipe", request=request)

    with pytest.raises(NetworkError):
        make_client(handler).runs.create("a valid goal")
    assert len(attempts) == 1


def test_rate_limit_headers_captured_on_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok", "version": "0.16.0"}, headers={
            "X-Request-ID": "rid9",
            "RateLimit-Limit": "60",
            "RateLimit-Remaining": "58",
            "RateLimit-Reset": "1793000000",
        })

    client = make_client(handler)
    client.health()
    info = client.last_response_info
    assert info is not None
    assert info.request_id == "rid9"
    assert info.rate_limit is not None
    assert info.rate_limit.remaining == 58
