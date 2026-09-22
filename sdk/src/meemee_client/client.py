"""MeemeeClient: typed, retry-aware access to the Meemee API (server v0.41.0).

Endpoint contract implemented here:
- GET  /health, GET /ready                          (unauthenticated, rate-limit exempt)
- POST /v1/runs                                     (scope runs:write)
- POST /v1/jobs, GET /v1/jobs/{id}, DELETE /v1/jobs/{id},
  GET /v1/jobs/{id}/events, GET /v1/jobs/{id}/stream (scopes jobs:write / jobs:read)
- POST /v1/tokens, DELETE /v1/tokens/{id}           (scope admin)
- GET  /v1/audit                                    (scope admin)
- GET  /metrics                                     (scope admin, Prometheus text)
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

import httpx
from typing_extensions import Self

from ._version import __version__
from .auth import AuthProvider, TokenAuth
from .errors import (
    ApiError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    NetworkError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ServerError,
    StreamError,
    ValidationError,
    WaitTimeoutError,
)
from .models import (
    KNOWN_SCOPES,
    TERMINAL_EVENT_KINDS,
    Approval,
    AuditEntry,
    AuditPage,
    CreatedJob,
    CreatedToken,
    CreatedWebhook,
    HealthStatus,
    Job,
    JobCancelResult,
    JobEvent,
    QuotaStatus,
    RateLimitInfo,
    ReadinessStatus,
    ResponseInfo,
    RevokedToken,
    RunReport,
    TokenMetadata,
    WebhookDelivery,
    WebhookSubscription,
)
from .retry import RetryPolicy
from .sse import SSEMessage, SSEParser

#: A synchronous run can take many minutes (bounded agent loop against a model),
#: so runs.get its own generous default read timeout.
DEFAULT_RUN_READ_TIMEOUT = 900.0
#: SSE streams heartbeat every 15s; a read stall beyond this means a dead peer.
DEFAULT_STREAM_READ_TIMEOUT = 90.0

_GOAL_MIN, _GOAL_MAX = 2, 20_000  # mirrors the server's RunRequest/JobRequest bounds


def _validate_goal(goal: str) -> None:
    if not isinstance(goal, str) or not (_GOAL_MIN <= len(goal) <= _GOAL_MAX):
        raise ValueError(f"goal must be a string of {_GOAL_MIN}-{_GOAL_MAX} characters")


def _iso_or_none(value: datetime | str | None, field_name: str) -> str | None:
    """Normalize a datetime/ISO-8601 string the way the server will parse it."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field_name} must be ISO 8601: {value!r}") from exc
        return value
    raise TypeError(f"{field_name} must be a datetime, an ISO 8601 string, or None")


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        # HTTP-date form is legal for Retry-After but the server always sends
        # integer seconds; treat anything else as absent rather than guessing.
        return None


class MeemeeClient:
    """Synchronous client for one Meemee API server.

    Parameters:
        base_url: Server root, e.g. ``http://127.0.0.1:8787``.
        auth: An AuthProvider, or a plain token string (wrapped in TokenAuth).
              None gives an unauthenticated client (health/ready only).
        retry: RetryPolicy; the default retries idempotent methods only.
        transport: Optional httpx transport (used by the test-suite).
        sleeper: Sleep function used between retries; injected by tests.
    """

    def __init__(
        self,
        base_url: str,
        auth: AuthProvider | str | None = None,
        *,
        timeout: httpx.Timeout | float | None = None,
        retry: RetryPolicy | None = None,
        transport: httpx.BaseTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        user_agent: str | None = None,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must be an absolute http(s) URL")
        if isinstance(auth, str):
            auth = TokenAuth(auth)
        self._auth = auth
        self._retry = retry or RetryPolicy()
        self._sleeper = sleeper
        resolved_timeout = timeout if timeout is not None else httpx.Timeout(60.0, connect=5.0)
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=resolved_timeout,
            transport=transport,
            headers={"User-Agent": user_agent or f"meemee-client/{__version__}"},
        )
        self.last_response_info: ResponseInfo | None = None
        self.runs = RunsResource(self)
        self.jobs = JobsResource(self)
        self.tokens = TokensResource(self)
        self.audit = AuditResource(self)
        self.quota = QuotaResource(self)
        self.approvals = ApprovalsResource(self)
        self.webhooks = WebhooksResource(self)

    # ------------------------------------------------------------------ basics

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def health(self) -> HealthStatus:
        """GET /health - unauthenticated liveness with the exact server version."""
        return HealthStatus.model_validate(self._request_json("GET", "/health"))

    def ready(self) -> ReadinessStatus:
        """GET /ready - database-backed readiness; raises ServerError on 503."""
        return ReadinessStatus.model_validate(self._request_json("GET", "/ready"))

    def metrics(self) -> str:
        """GET /metrics - Prometheus exposition text (admin scope)."""
        response = self._request("GET", "/metrics")
        return response.text

    # ------------------------------------------------------------- http core

    def _headers(self, extra: dict[str, str] | None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._auth is not None:
            headers["Authorization"] = self._auth.authorization_header()
        if extra:
            headers.update(extra)
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | float | None = None,
    ) -> httpx.Response:
        policy = self._retry
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._http.request(
                    method, path, params=params, json=json_body,
                    headers=self._headers(headers),
                    **({"timeout": timeout} if timeout is not None else {}),
                )
            except httpx.TransportError as exc:
                error = NetworkError(f"{method} {path} failed before a response: {exc}")
                if policy.can_retry(method, attempt):
                    self._sleeper(policy.delay(attempt))
                    continue
                raise error from exc
            self.last_response_info = ResponseInfo(
                request_id=response.headers.get("X-Request-ID"),
                rate_limit=RateLimitInfo.from_headers(response.headers),
            )
            if (
                response.status_code >= 400
                and policy.is_retryable_status(response.status_code)
                and policy.can_retry(method, attempt)
            ):
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                self._sleeper(policy.delay(attempt, retry_after))
                continue
            if response.status_code >= 400:
                raise _map_error(response)
            return response

    def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._request(method, path, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            raise ApiError(
                f"{method} {path} returned a non-JSON {response.status_code} response",
                status_code=response.status_code,
                request_id=response.headers.get("X-Request-ID"),
            ) from exc

    def _stream(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
    ):
        """Open a streaming response, mapping an error status before returning it."""
        stream = self._http.stream(
            method, path, params=params, headers=self._headers(headers),
            timeout=timeout or httpx.Timeout(DEFAULT_STREAM_READ_TIMEOUT, connect=5.0),
        )
        response = stream.__enter__()
        self.last_response_info = ResponseInfo(
            request_id=response.headers.get("X-Request-ID"),
            rate_limit=RateLimitInfo.from_headers(response.headers),
        )
        if response.status_code >= 400:
            response.read()
            error = _map_error(response)
            stream.__exit__(None, None, None)
            raise error
        return _StreamGuard(stream, response)


class _StreamGuard:
    """Return the entered stream response while keeping its context manager."""

    def __init__(self, stream: Any, response: httpx.Response) -> None:
        self._stream = stream
        self.response = response

    def __enter__(self) -> httpx.Response:
        return self.response

    def __exit__(self, *exc_info: object) -> None:
        self._stream.__exit__(*exc_info)


def _map_error(response: httpx.Response) -> ApiError:
    request_id = response.headers.get("X-Request-ID")
    status = response.status_code
    detail: Any = None
    body_request_id: str | None = None
    try:
        body = response.json()
        detail = body.get("detail")
        body_request_id = body.get("request_id")
    except ValueError:
        detail = response.text[:500] if response.text else None
    rid = request_id or body_request_id
    if isinstance(detail, list):
        summary = "; ".join(
            f"{'.'.join(str(p) for p in issue.get('loc', []))}: {issue.get('msg', '')}"
            for issue in detail
            if isinstance(issue, dict)
        ) or "request validation failed"
        message = f"HTTP {status}: {summary}"
    else:
        message = f"HTTP {status}: {detail}" if detail else f"HTTP {status} {response.reason_phrase}"
    if rid:
        message = f"{message} (request_id={rid})"

    if status == 400:
        return BadRequestError(message, status_code=status, request_id=rid, detail=detail)
    if status == 401:
        return AuthenticationError(
            message, status_code=status, request_id=rid, detail=detail,
            www_authenticate=response.headers.get("WWW-Authenticate"),
        )
    if status == 403:
        missing_scope = None
        if isinstance(detail, str) and detail.startswith("missing scope: "):
            missing_scope = detail.removeprefix("missing scope: ").strip()
        return PermissionDeniedError(message, status_code=status, request_id=rid, detail=detail, missing_scope=missing_scope)
    if status == 404:
        return NotFoundError(message, status_code=status, request_id=rid, detail=detail)
    if status == 409:
        return ConflictError(message, status_code=status, request_id=rid, detail=detail)
    if status == 422:
        issues = detail if isinstance(detail, list) else None
        return ValidationError(message, status_code=status, request_id=rid, detail=detail, issues=issues)
    if status == 429:
        return RateLimitError(
            message, status_code=status, request_id=rid, detail=detail,
            retry_after=_parse_retry_after(response.headers.get("Retry-After")),
        )
    return ServerError(message, status_code=status, request_id=rid, detail=detail)


# ----------------------------------------------------------------- resources


class RunsResource:
    """Synchronous agent runs (scope: runs:write)."""

    def __init__(self, client: MeemeeClient) -> None:
        self._client = client

    def list(self, *, before: str | None = None, limit: int = 100) -> list[RunReport]:
        params: dict[str, Any] = {"limit": limit}
        if before is not None: params["before"] = before
        payload = self._client._request_json("GET", "/v1/runs", params=params)
        return [RunReport.model_validate(item) for item in payload["runs"]]

    def get(self, run_id: str) -> RunReport:
        return RunReport.model_validate(self._client._request_json("GET", f"/v1/runs/{run_id}"))

    def create(
        self,
        goal: str,
        *,
        approve_writes: bool = False,
        timeout: httpx.Timeout | float | None = None,
    ) -> RunReport:
        """POST /v1/runs - run the agent loop to completion and return its report.

        ``approve_writes=True`` lets the agent's write/execute tools act without
        an interactive approval gate; leave it False for a read-only run.
        The default read timeout is 15 minutes because a full agent loop is slow.

        Note: POST is not retried automatically (the server has no idempotency
        keys). A NetworkError here can mean the run still executed server-side.
        """
        _validate_goal(goal)
        payload = self._client._request_json(
            "POST", "/v1/runs",
            json_body={"goal": goal, "approve_writes": approve_writes},
            timeout=timeout if timeout is not None else httpx.Timeout(DEFAULT_RUN_READ_TIMEOUT, connect=5.0),
        )
        return RunReport.model_validate(payload)


class JobsResource:
    """Durable queued jobs (scopes: jobs:write to create/cancel, jobs:read to inspect)."""

    def __init__(self, client: MeemeeClient) -> None:
        self._client = client

    def create(self, goal: str, *, run_at: datetime | str | None = None) -> CreatedJob:
        """POST /v1/jobs - enqueue a goal, optionally scheduled for a future run_at.

        Workers claim due jobs atomically and retry failures up to the job's
        max_attempts (server default 3). Not auto-retried; see RunsResource.create.
        """
        _validate_goal(goal)
        body: dict[str, Any] = {"goal": goal}
        scheduled = _iso_or_none(run_at, "run_at")
        if scheduled is not None:
            body["run_at"] = scheduled
        payload = self._client._request_json("POST", "/v1/jobs", json_body=body)
        return CreatedJob.model_validate(payload)

    def list(
        self, *, status: str | None = None, before: str | None = None, limit: int = 100
    ) -> list[Job]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be 1-500")
        params: dict[str, Any] = {"limit": limit}
        if status is not None: params["status"] = status
        if before is not None: params["before"] = before
        payload = self._client._request_json("GET", "/v1/jobs", params=params)
        return [Job.model_validate(item) for item in payload["jobs"]]

    def get(self, job_id: str) -> Job:
        """GET /v1/jobs/{id} - current state; raises NotFoundError for unknown ids."""
        return Job.model_validate(self._client._request_json("GET", f"/v1/jobs/{job_id}"))

    def cancel(self, job_id: str) -> JobCancelResult:
        """DELETE /v1/jobs/{id} - request cancellation.

        A queued job becomes ``cancelled`` immediately; a running job becomes
        ``cancel_requested`` and the worker stops it cooperatively between agent
        steps. Cancelling an already-cancelled job is an idempotent no-op that
        returns its current status; ConflictError is raised only when the job
        is done or failed. (Verified against the live server.)
        """
        payload = self._client._request_json("DELETE", f"/v1/jobs/{job_id}")
        return JobCancelResult.model_validate(payload)

    def events(self, job_id: str, *, after: int = 0) -> list[JobEvent]:
        """GET /v1/jobs/{id}/events - durable events with sequence > ``after``."""
        payload = self._client._request_json("GET", f"/v1/jobs/{job_id}/events", params={"after": after})
        return [JobEvent.model_validate(item) for item in payload["events"]]

    def iter_events(self, job_id: str, *, after: int = 0) -> Iterator[JobEvent]:
        """Drain the currently stored events, following the sequence cursor.

        Stops when no further events are stored right now; for live following
        use stream_events().
        """
        cursor = after
        while True:
            batch = self.events(job_id, after=cursor)
            if not batch:
                return
            for event in batch:
                cursor = max(cursor, event.sequence)
                yield event

    def stream_events(
        self,
        job_id: str,
        *,
        after: int = 0,
        reconnect: bool = True,
        max_reconnects: int = 6,
        read_timeout: float = DEFAULT_STREAM_READ_TIMEOUT,
        on_heartbeat: Callable[[str], None] | None = None,
    ) -> Iterator[JobEvent]:
        """GET /v1/jobs/{id}/stream - follow live progress over SSE with resume.

        Yields JobEvents in order. On a dropped connection the stream resumes
        with a Last-Event-ID header, so no event is delivered twice and none is
        skipped. Returns when the job reaches a terminal event (done, failed or
        cancelled); the server itself closes the stream only after done/failed,
        so the cancelled case is closed client-side. Heartbeat comments are
        surfaced through ``on_heartbeat`` instead of the event stream.

        Raises NotFoundError for an unknown job, StreamError for a server error
        frame or a malformed event, and NetworkError when the reconnect budget
        is exhausted.
        """
        cursor = max(after, 0)
        reconnects_left = max_reconnects
        backoff = 1.0
        while True:
            headers = {"Accept": "text/event-stream"}
            if cursor:
                headers["Last-Event-ID"] = str(cursor)
            try:
                with self._client._stream(
                    "GET", f"/v1/jobs/{job_id}/stream",
                    params={"after": cursor}, headers=headers,
                    timeout=httpx.Timeout(read_timeout, connect=5.0),
                ) as response:
                    parser = SSEParser()
                    for chunk in response.iter_bytes():
                        for message in parser.feed(chunk):
                            terminal = self._handle_stream_message(message, job_id, on_heartbeat)
                            if terminal is None:
                                continue
                            event, is_terminal = terminal
                            cursor = max(cursor, event.sequence)
                            yield event
                            if is_terminal:
                                return
                    # Clean EOF: the server closes the stream only after a
                    # terminal done/failed event (or its error frame).
                    return
            except httpx.TransportError as exc:
                if not reconnect or reconnects_left <= 0:
                    raise NetworkError(
                        f"job stream for {job_id} interrupted and the reconnect budget is "
                        f"exhausted (last delivered event id {cursor}): {exc}"
                    ) from exc
                reconnects_left -= 1
                self._client._sleeper(min(backoff, 30.0))
                backoff *= 2.0

    @staticmethod
    def _handle_stream_message(
        message: SSEMessage,
        job_id: str,
        on_heartbeat: Callable[[str], None] | None,
    ) -> tuple[JobEvent, bool] | None:
        if message.kind == "comment":
            if on_heartbeat is not None:
                on_heartbeat(message.comment or "")
            return None
        if message.event == "error":
            # Terminal server-side failure frame, e.g. the job disappeared.
            raise StreamError(f"server sent an error frame on job {job_id}: {message.data}")
        try:
            data = json.loads(message.data or "")
        except ValueError as exc:
            raise StreamError(f"malformed SSE data frame on job {job_id}: {message.data!r}") from exc
        try:
            event = JobEvent.model_validate(data)
        except ValueError as exc:
            raise StreamError(f"unparseable job event on job {job_id}: {data!r}") from exc
        return event, event.kind in TERMINAL_EVENT_KINDS

    def wait(
        self,
        job_id: str,
        *,
        timeout: float | None = None,
        poll_interval: float = 2.0,
    ) -> Job:
        """Poll GET /v1/jobs/{id} until the job reaches a terminal state.

        Terminal states: done, failed, cancelled. Prefer stream_events() for
        live progress; wait() is the simple blocking form. Raises
        WaitTimeoutError after ``timeout`` seconds (no timeout by default).
        """
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            job = self.get(job_id)
            if job.is_terminal:
                return job
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WaitTimeoutError(
                        f"job {job_id} is still {job.status.value} after {timeout} seconds"
                    )
                self._client._sleeper(min(poll_interval, remaining))
            else:
                self._client._sleeper(poll_interval)


class TokensResource:
    """Scoped, revocable API token administration (scope: admin)."""

    def __init__(self, client: MeemeeClient) -> None:
        self._client = client

    def create(
        self,
        name: str,
        scopes: set[str] | frozenset[str] | list[str],
        *,
        expires_at: datetime | str | None = None,
    ) -> CreatedToken:
        """POST /v1/tokens - mint a token. The raw token is returned exactly once;
        store it immediately - the server keeps only its SHA-256 digest.

        scopes must be a non-empty subset of {"admin", "runs:write", "jobs:read",
        "jobs:write"}; the same check runs server-side.
        """
        if not name or not name.strip() or len(name) > 100:
            raise ValueError("name must be 1-100 characters")
        scope_set = set(scopes)
        if not scope_set:
            raise ValueError("at least one scope is required")
        unknown = scope_set - KNOWN_SCOPES
        if unknown:
            raise ValueError(f"unknown scopes: {sorted(unknown)} (allowed: {sorted(KNOWN_SCOPES)})")
        body: dict[str, Any] = {"name": name, "scopes": sorted(scope_set)}
        expiry = _iso_or_none(expires_at, "expires_at")
        if expiry is not None:
            body["expires_at"] = expiry
        payload = self._client._request_json("POST", "/v1/tokens", json_body=body)
        return CreatedToken.model_validate(payload)

    def list(
        self, *, revoked: bool | None = None, before: str | None = None, limit: int = 100
    ) -> list[TokenMetadata]:
        params: dict[str, Any] = {"limit": limit}
        if revoked is not None: params["revoked"] = revoked
        if before is not None: params["before"] = before
        payload = self._client._request_json("GET", "/v1/tokens", params=params)
        return [TokenMetadata.model_validate(item) for item in payload["tokens"]]

    def revoke(self, token_id: str) -> RevokedToken:
        """DELETE /v1/tokens/{id} - revoke an active token (idempotent-safe:
        raises NotFoundError when the id is unknown or already revoked)."""
        payload = self._client._request_json("DELETE", f"/v1/tokens/{token_id}")
        return RevokedToken.model_validate(payload)


class AuditResource:
    """Tamper-evident SHA-256 audit chain (scope: admin).

    Every list() response is chain-verified by the server before it answers;
    a broken chain surfaces as ServerError, not as a page with verified=False.
    """

    def __init__(self, client: MeemeeClient) -> None:
        self._client = client

    def list(self, *, after: int = 0, limit: int = 100) -> AuditPage:
        """GET /v1/audit - entries with sequence > ``after``, oldest first.
        The server caps limit at 500."""
        if not (1 <= limit <= 500):
            raise ValueError("limit must be 1-500 (the server caps at 500)")
        payload = self._client._request_json(
            "GET", "/v1/audit", params={"after": after, "limit": limit}
        )
        return AuditPage.model_validate(payload)

    def iter_entries(self, *, after: int = 0, limit: int = 100) -> Iterator[AuditEntry]:
        """Walk the whole chain from ``after``, following the sequence cursor."""
        cursor = after
        while True:
            page = self.list(after=cursor, limit=limit)
            if not page.entries:
                return
            for entry in page.entries:
                cursor = max(cursor, entry.sequence)
                yield entry


class QuotaResource:
    def __init__(self, client: MeemeeClient) -> None:
        self._client = client

    def get(self) -> QuotaStatus:
        return QuotaStatus.model_validate(self._client._request_json("GET", "/v1/quota"))

    def set(self, principal_id: str, daily_jobs: int) -> QuotaStatus:
        if not 1 <= daily_jobs <= 1_000_000:
            raise ValueError("daily_jobs must be 1-1000000")
        payload = self._client._request_json(
            "PUT", f"/v1/quota/{principal_id}", json_body={"daily_jobs": daily_jobs}
        )
        return QuotaStatus.model_validate(payload)


class ApprovalsResource:
    def __init__(self, client: MeemeeClient) -> None:
        self._client = client

    def list(self, principal_id: str) -> list[Approval]:
        payload = self._client._request_json("GET", f"/v1/approvals/{principal_id}")
        return [Approval.model_validate(item) for item in payload["approvals"]]

    def grant(self, principal_id: str, tool: str, expires_at: datetime | str | None = None) -> dict:
        body = {"tool": tool, "expires_at": _iso_or_none(expires_at, "expires_at")}
        return self._client._request_json("PUT", f"/v1/approvals/{principal_id}", json_body=body)

    def revoke(self, principal_id: str, tool: str) -> dict:
        return self._client._request_json("DELETE", f"/v1/approvals/{principal_id}/{tool}")


class WebhooksResource:
    def __init__(self, client: MeemeeClient) -> None:
        self._client = client

    def create(self, url: str, events: set[str], *, fields: set[str] | None = None, headers: dict[str,str] | None = None) -> CreatedWebhook:
        if not events:
            raise ValueError("at least one event is required")
        payload = self._client._request_json("POST", "/v1/webhooks", json_body={
            "url": url, "events": sorted(events), "fields": sorted(fields or ()), "headers": headers or {},
        })
        return CreatedWebhook.model_validate(payload)

    def list(self) -> list[WebhookSubscription]:
        payload = self._client._request_json("GET", "/v1/webhooks")
        return [WebhookSubscription.model_validate(item) for item in payload["webhooks"]]

    def delete(self, webhook_id: str) -> dict:
        return self._client._request_json("DELETE", f"/v1/webhooks/{webhook_id}")

    def rotate_secret(self, webhook_id: str) -> CreatedWebhook:
        payload = self._client._request_json("POST", f"/v1/webhooks/{webhook_id}/rotate-secret")
        return CreatedWebhook.model_validate(payload)

    def deliveries(self, *, status: str | None = None, after: str | None = None, limit: int = 100) -> list[WebhookDelivery]:
        params = {"limit": limit}
        if status is not None: params["status"] = status
        if after is not None: params["after"] = after
        payload = self._client._request_json("GET", "/v1/webhook-deliveries", params=params)
        return [WebhookDelivery.model_validate(item) for item in payload["deliveries"]]

    def replay(self, delivery_id: str) -> dict:
        return self._client._request_json("POST", f"/v1/webhook-deliveries/{delivery_id}/replay")
