"""MeemeeClient: typed, retry-aware access to the current Meemee API.

Endpoint contract implemented here:
- GET  /health, GET /ready                          (unauthenticated, rate-limit exempt)
- POST /v1/runs                                     (scope runs:write)
- POST /v1/jobs, GET /v1/jobs/{id}, DELETE /v1/jobs/{id},
  GET /v1/jobs/{id}/events, GET /v1/jobs/{id}/stream,
  GET /v1/jobs (list), WS /v1/jobs/{id}/ws           (scopes jobs:write / jobs:read)
- POST/GET /v1/tokens, DELETE /v1/tokens/{id}       (scope admin)
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

from . import _ws
from ._version import __version__
from .auth import AuthProvider, TokenAuth
from .errors import (
    ApiError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    IdempotencyConflictError,
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
    CheckInPreferences,
    CheckInTickResult,
    CheckInUpdateResult,
    CompanionChatReply,
    CompanionCheckIn,
    CompanionConversation,
    CompanionFact,
    CompanionMessage,
    CompanionUser,
    CreatedJob,
    CreatedToken,
    CreatedWebhook,
    FactRetireResult,
    HealthStatus,
    Job,
    JobCancelResult,
    JobEvent,
    PersonaConfig,
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
        self.companion = CompanionResource(self)

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
        """GET /ready - parse both ready (200) and diagnostic not-ready (503) bodies."""
        return ReadinessStatus.model_validate(self._request_json("GET", "/ready", allowed_statuses={503}))

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
        allowed_statuses: set[int] | None = None,
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
                retryable_write = method.upper() == "POST" and bool(headers and headers.get("Idempotency-Key"))
                if (policy.can_retry(method, attempt) or (retryable_write and attempt < policy.max_attempts)):
                    self._sleeper(policy.delay(attempt))
                    continue
                raise error from exc
            self.last_response_info = ResponseInfo(
                request_id=response.headers.get("X-Request-ID"),
                rate_limit=RateLimitInfo.from_headers(response.headers),
            )
            retryable_write = method.upper() == "POST" and bool(headers and headers.get("Idempotency-Key"))
            keyed_status_retry = retryable_write and response.status_code != 429 and attempt < policy.max_attempts
            if (
                response.status_code >= 400
                and policy.is_retryable_status(response.status_code)
                and (policy.can_retry(method, attempt) or keyed_status_retry)
            ):
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                self._sleeper(policy.delay(attempt, retry_after))
                continue
            if response.status_code >= 400 and response.status_code not in (allowed_statuses or set()):
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
        error_type = IdempotencyConflictError if isinstance(detail, str) and "idempotency" in detail.lower() else ConflictError
        return error_type(message, status_code=status, request_id=rid, detail=detail)
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

    def page(self, *, cursor: str | None = None, before: str | None = None, limit: int = 100) -> tuple[list[RunReport], str | None]:
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None: params["cursor"] = cursor
        if before is not None: params["before"] = before
        payload = self._client._request_json("GET", "/v1/runs", params=params)
        return [RunReport.model_validate(item) for item in payload["runs"]], payload.get("next_cursor")

    def list(self, *, before: str | None = None, limit: int = 100) -> list[RunReport]:
        return self.page(before=before, limit=limit)[0]

    def iter_all(self, *, limit: int = 100) -> Iterator[RunReport]:
        cursor = None
        while True:
            items, cursor = self.page(cursor=cursor, limit=limit)
            yield from items
            if cursor is None: return

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

        Note: synchronous runs are not idempotent, so POST is not retried automatically.
        A NetworkError here can mean the run still executed server-side.
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

    def create(self, goal: str, *, run_at: datetime | str | None = None, idempotency_key: str | None = None) -> CreatedJob:
        """POST /v1/jobs - enqueue a goal, optionally scheduled for a future run_at.

        Workers claim due jobs atomically and retry failures up to the job's
        max_attempts (server default 3). Not auto-retried; see RunsResource.create.
        """
        _validate_goal(goal)
        if idempotency_key is not None and (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200):
            raise ValueError("idempotency_key must contain 1-200 characters")
        body: dict[str, Any] = {"goal": goal}
        scheduled = _iso_or_none(run_at, "run_at")
        if scheduled is not None:
            body["run_at"] = scheduled
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        payload = self._client._request_json("POST", "/v1/jobs", json_body=body, headers=headers)
        return CreatedJob.model_validate(payload)

    def page(self, *, status: str | None = None, cursor: str | None = None, before: str | None = None, limit: int = 100) -> tuple[list[Job], str | None]:
        if not 1 <= limit <= 500: raise ValueError("limit must be 1-500")
        params: dict[str, Any] = {"limit": limit}
        if status is not None: params["status"] = status
        if cursor is not None: params["cursor"] = cursor
        if before is not None: params["before"] = before
        payload = self._client._request_json("GET", "/v1/jobs", params=params)
        return [Job.model_validate(item) for item in payload["jobs"]], payload.get("next_cursor")

    def list(self, *, status: str | None = None, before: str | None = None, limit: int = 100) -> list[Job]:
        return self.page(status=status, before=before, limit=limit)[0]

    def iter_all(self, *, status: str | None = None, limit: int = 100) -> Iterator[Job]:
        cursor = None
        while True:
            items, cursor = self.page(status=status, cursor=cursor, limit=limit)
            yield from items
            if cursor is None: return

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

    def stream_ws(
        self,
        job_id: str,
        *,
        after: int = 0,
        reconnect: bool = True,
        max_reconnects: int = 6,
        open_timeout: float = 10.0,
        ping_interval: float | None = 20.0,
        ping_timeout: float | None = 20.0,
    ) -> Iterator[JobEvent]:
        """GET /v1/jobs/{id}/ws - follow live progress over WebSocket with resume.

        Yields JobEvents in sequence order and returns after a terminal event
        (done, failed or cancelled) or the server's normal 1000 close. A dropped
        connection reconnects with ``?after=<last delivered sequence>``, so no
        event is repeated or skipped; the server sends no idle frames, so dead
        peers are detected by WebSocket ping/pong (``ping_interval``/``ping_timeout``).

        Raises AuthenticationError (4401), PermissionDeniedError (4403 or a
        pre-handshake HTTP 403), NotFoundError (4404), BadRequestError (4400),
        StreamError for a malformed frame, and NetworkError once the reconnect
        budget is spent. Needs the ``ws`` extra (``websockets``).
        """
        websockets = _ws.require_websockets()
        from websockets.exceptions import ConnectionClosed, InvalidStatus
        from websockets.sync.client import connect

        cursor = max(after, 0)
        reconnects_left = max_reconnects
        backoff = 1.0
        base = str(self._client._http.base_url)
        while True:
            failure: BaseException | None = None
            try:
                with connect(
                    _ws.ws_url(base, job_id, cursor),
                    additional_headers=self._client._headers(None),
                    user_agent_header=self._client._http.headers.get("User-Agent"),
                    open_timeout=open_timeout,
                    ping_interval=ping_interval,
                    ping_timeout=ping_timeout,
                    proxy=None,
                ) as connection:
                    while True:
                        try:
                            frame = connection.recv()
                        except ConnectionClosed as closed:
                            code = closed.rcvd.code if closed.rcvd is not None else None
                            reason = closed.rcvd.reason if closed.rcvd is not None else ""
                            rejection = _ws.rejection_error(code, reason, job_id)
                            if rejection is not None:
                                raise rejection from None
                            if code == _ws.NORMAL_CLOSE:
                                return
                            failure = closed
                            break
                        event, is_terminal = _ws.parse_frame(frame, job_id)
                        if event.sequence <= cursor:
                            continue
                        cursor = event.sequence
                        yield event
                        if is_terminal:
                            return
            except InvalidStatus as exc:
                status = exc.response.status_code
                mapped = _ws.handshake_error(status, job_id)
                if mapped is not None:
                    raise mapped from exc
                failure = exc
            except (OSError, TimeoutError, websockets.exceptions.InvalidHandshake) as exc:
                failure = exc
            if not reconnect or reconnects_left <= 0:
                raise NetworkError(
                    f"job WebSocket for {job_id} interrupted and the reconnect budget is "
                    f"exhausted (last delivered event id {cursor}): {failure}"
                ) from failure
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

    def page(self, *, revoked: bool | None = None, cursor: str | None = None, before: str | None = None, limit: int = 100) -> tuple[list[TokenMetadata], str | None]:
        params: dict[str, Any] = {"limit": limit}
        if revoked is not None: params["revoked"] = revoked
        if cursor is not None: params["cursor"] = cursor
        if before is not None: params["before"] = before
        payload = self._client._request_json("GET", "/v1/tokens", params=params)
        return [TokenMetadata.model_validate(item) for item in payload["tokens"]], payload.get("next_cursor")

    def list(self, *, revoked: bool | None = None, before: str | None = None, limit: int = 100) -> list[TokenMetadata]:
        return self.page(revoked=revoked, before=before, limit=limit)[0]

    def iter_all(self, *, revoked: bool | None = None, limit: int = 100) -> Iterator[TokenMetadata]:
        cursor = None
        while True:
            items, cursor = self.page(revoked=revoked, cursor=cursor, limit=limit)
            yield from items
            if cursor is None: return

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

    def page(self, *, after: int = 0, limit: int = 100) -> tuple[AuditPage, str | None]:
        if not (1 <= limit <= 500):
            raise ValueError("limit must be 1-500 (the server caps at 500)")
        payload = self._client._request_json(
            "GET", "/v1/audit", params={"after": after, "limit": limit}
        )
        return AuditPage.model_validate(payload), payload.get("next_cursor")

    def list(self, *, after: int = 0, limit: int = 100) -> AuditPage:
        """GET /v1/audit - entries with sequence > ``after``, oldest first."""
        return self.page(after=after, limit=limit)[0]

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
        principal = httpx.URL("https://local").copy_with(path=f"/{principal_id}").raw_path.decode().lstrip("/")
        payload = self._client._request_json("GET", f"/v1/approvals/{principal}")
        return [Approval.model_validate(item) for item in payload["approvals"]]

    def grant(self, principal_id: str, tool: str, expires_at: datetime | str | None = None, *, argument_constraints: dict[str, Any] | None = None) -> dict:
        if argument_constraints is not None and not isinstance(argument_constraints, dict):
            raise TypeError("argument_constraints must be a dict or None")
        body = {"tool": tool, "expires_at": _iso_or_none(expires_at, "expires_at"), "argument_constraints": argument_constraints}
        principal = httpx.URL("https://local").copy_with(path=f"/{principal_id}").raw_path.decode().lstrip("/")
        return self._client._request_json("PUT", f"/v1/approvals/{principal}", json_body=body)

    def revoke(self, principal_id: str, tool: str) -> dict:
        root = httpx.URL("https://local")
        principal = root.copy_with(path=f"/{principal_id}").raw_path.decode().lstrip("/")
        encoded_tool = root.copy_with(path=f"/{tool}").raw_path.decode().lstrip("/")
        return self._client._request_json("DELETE", f"/v1/approvals/{principal}/{encoded_tool}")


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

    def deliveries_page(self, *, status: str | None = None, after: str | None = None, cursor: str | None = None, limit: int = 100) -> tuple[list[WebhookDelivery], str | None]:
        params = {"limit": limit}
        if status is not None: params["status"] = status
        if after is not None: params["after"] = after
        if cursor is not None: params["cursor"] = cursor
        payload = self._client._request_json("GET", "/v1/webhook-deliveries", params=params)
        return [WebhookDelivery.model_validate(item) for item in payload["deliveries"]], payload.get("next_cursor")

    def deliveries(self, *, status: str | None = None, after: str | None = None, limit: int = 100) -> list[WebhookDelivery]:
        return self.deliveries_page(status=status, after=after, limit=limit)[0]

    def iter_deliveries(self, *, status: str | None = None, limit: int = 100) -> Iterator[WebhookDelivery]:
        cursor = None
        while True:
            items, cursor = self.deliveries_page(status=status, cursor=cursor, limit=limit)
            yield from items
            if cursor is None: return

    def replay(self, delivery_id: str) -> dict:
        return self._client._request_json("POST", f"/v1/webhook-deliveries/{delivery_id}/replay")


class CompanionResource:
    """Companion layer: profiles, persona, facts, chat and check-ins.

    Scopes: companion:read for inspection, companion:write for mutation and
    chat, admin for the delivery tick.
    """

    def __init__(self, client: MeemeeClient) -> None:
        self._client = client

    def list_users(self, *, limit: int = 100) -> list[CompanionUser]:
        payload = self._client._request_json("GET", "/v1/companion/users", params={"limit": limit})
        return [CompanionUser.model_validate(item) for item in payload["users"]]

    def upsert_user(
        self,
        user_id: str,
        display_name: str,
        *,
        timezone: str = "UTC",
        persona: PersonaConfig | None = None,
        checkins: CheckInPreferences | None = None,
    ) -> CompanionUser:
        body: dict[str, Any] = {"display_name": display_name, "timezone": timezone}
        if persona is not None:
            body["persona"] = persona.model_dump()
        if checkins is not None:
            body["checkins"] = checkins.model_dump()
        payload = self._client._request_json("PUT", f"/v1/companion/users/{user_id}", json_body=body)
        return CompanionUser.model_validate(payload)

    def get_user(self, user_id: str) -> CompanionUser:
        return CompanionUser.model_validate(self._client._request_json("GET", f"/v1/companion/users/{user_id}"))

    def update_persona(self, user_id: str, persona: PersonaConfig) -> CompanionUser:
        payload = self._client._request_json(
            "PUT", f"/v1/companion/users/{user_id}/persona", json_body={"persona": persona.model_dump()}
        )
        return CompanionUser.model_validate(payload)

    def update_checkins(self, user_id: str, checkins: CheckInPreferences) -> CheckInUpdateResult:
        payload = self._client._request_json(
            "PUT", f"/v1/companion/users/{user_id}/checkins", json_body={"checkins": checkins.model_dump()}
        )
        return CheckInUpdateResult.model_validate(payload)

    def list_facts(self, user_id: str, *, query: str | None = None, limit: int = 200) -> list[CompanionFact]:
        params: dict[str, Any] = {"limit": limit}
        if query is not None:
            params["query"] = query
        payload = self._client._request_json("GET", f"/v1/companion/users/{user_id}/facts", params=params)
        return [CompanionFact.model_validate(item) for item in payload["facts"]]

    def add_fact(self, user_id: str, text: str, *, category: str = "general", confidence: float = 1.0) -> CompanionFact:
        payload = self._client._request_json(
            "POST", f"/v1/companion/users/{user_id}/facts",
            json_body={"category": category, "text": text, "confidence": confidence},
        )
        return CompanionFact.model_validate(payload)

    def retire_fact(self, user_id: str, fact_id: int) -> FactRetireResult:
        payload = self._client._request_json("DELETE", f"/v1/companion/users/{user_id}/facts/{fact_id}")
        return FactRetireResult.model_validate(payload)

    def chat(
        self,
        user_id: str,
        text: str,
        *,
        channel: str = "local",
        conversation_id: str | None = None,
        timeout: httpx.Timeout | float | None = None,
    ) -> CompanionChatReply:
        """POST /v1/companion/chat - one conversational turn with durable memory.

        Not auto-retried: a network failure can mean the turn still executed.
        """
        if not text.strip():
            raise ValueError("text must not be empty")
        body: dict[str, Any] = {"user_id": user_id, "text": text, "channel": channel}
        if conversation_id is not None:
            body["conversation_id"] = conversation_id
        payload = self._client._request_json(
            "POST", "/v1/companion/chat", json_body=body,
            timeout=timeout if timeout is not None else httpx.Timeout(300.0, connect=5.0),
        )
        return CompanionChatReply.model_validate(payload)

    def list_conversations(self, user_id: str, *, limit: int = 50) -> list[CompanionConversation]:
        payload = self._client._request_json(
            "GET", f"/v1/companion/users/{user_id}/conversations", params={"limit": limit}
        )
        return [CompanionConversation.model_validate(item) for item in payload["conversations"]]

    def messages(self, conversation_id: str, *, limit: int = 100) -> list[CompanionMessage]:
        payload = self._client._request_json(
            "GET", f"/v1/companion/conversations/{conversation_id}/messages", params={"limit": limit}
        )
        return [CompanionMessage.model_validate(item) for item in payload["messages"]]

    def plan_checkin(self, user_id: str) -> CompanionCheckIn:
        payload = self._client._request_json("POST", f"/v1/companion/users/{user_id}/checkins/plan")
        return CompanionCheckIn.model_validate(payload)

    def list_checkins(self, user_id: str, *, status: str | None = None, limit: int = 50) -> list[CompanionCheckIn]:
        params: dict[str, Any] = {"limit": limit}
        if status is not None:
            params["status"] = status
        payload = self._client._request_json("GET", f"/v1/companion/users/{user_id}/checkins", params=params)
        return [CompanionCheckIn.model_validate(item) for item in payload["checkins"]]

    def tick(self) -> CheckInTickResult:
        """POST /v1/companion/checkins/tick (admin) - plan slots and deliver due check-ins now."""
        payload = self._client._request_json("POST", "/v1/companion/checkins/tick")
        return CheckInTickResult.model_validate(payload)
