"""AsyncMeemeeClient: the asyncio twin of MeemeeClient.

Same endpoints, models, error types and retry policy as the synchronous
client; every network method is a coroutine and every iterator is an async
iterator. Streams resume the same way: SSE with ``Last-Event-ID`` and
WebSocket with ``?after=<last sequence>``.

Authentication: TokenAuth is used directly. A provider exposing
``async authorization_header_async()`` is awaited. Any other provider (for
example OIDCClientCredentialsAuth, whose refresh does blocking HTTP) runs in a
worker thread so a token refresh never blocks the event loop.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import httpx
from typing_extensions import Self

from . import _ws
from ._version import __version__
from .auth import AuthProvider, TokenAuth
from .client import (
    DEFAULT_RUN_READ_TIMEOUT,
    DEFAULT_STREAM_READ_TIMEOUT,
    JobsResource,
    _iso_or_none,
    _map_error,
    _parse_retry_after,
    _validate_goal,
)
from .errors import ApiError, NetworkError, WaitTimeoutError
from .models import (
    KNOWN_SCOPES,
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
from .sse import SSEParser

AsyncSleeper = Callable[[float], Awaitable[None]]


def _quote_segment(value: str) -> str:
    return httpx.URL("https://local").copy_with(path=f"/{value}").raw_path.decode().lstrip("/")


class AsyncMeemeeClient:
    """Asyncio client for one Meemee API server.

    Parameters match MeemeeClient, except ``transport`` is an
    ``httpx.AsyncBaseTransport`` and ``sleeper`` is an async callable
    (default ``asyncio.sleep``). Use as ``async with AsyncMeemeeClient(...)``.
    """

    def __init__(
        self,
        base_url: str,
        auth: AuthProvider | str | None = None,
        *,
        timeout: httpx.Timeout | float | None = None,
        retry: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleeper: AsyncSleeper = asyncio.sleep,
        user_agent: str | None = None,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("base_url must be an absolute http(s) URL")
        if isinstance(auth, str):
            auth = TokenAuth(auth)
        self._auth = auth
        self._retry = retry or RetryPolicy()
        self._sleeper = sleeper
        self._base_url = base_url.rstrip("/")
        self._user_agent = user_agent or f"meemee-client/{__version__}"
        resolved_timeout = timeout if timeout is not None else httpx.Timeout(60.0, connect=5.0)
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=resolved_timeout,
            transport=transport,
            headers={"User-Agent": self._user_agent},
        )
        self.last_response_info: ResponseInfo | None = None
        self.runs = AsyncRunsResource(self)
        self.jobs = AsyncJobsResource(self)
        self.tokens = AsyncTokensResource(self)
        self.audit = AsyncAuditResource(self)
        self.quota = AsyncQuotaResource(self)
        self.approvals = AsyncApprovalsResource(self)
        self.webhooks = AsyncWebhooksResource(self)
        self.companion = AsyncCompanionResource(self)

    # ------------------------------------------------------------------ basics

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def health(self) -> HealthStatus:
        return HealthStatus.model_validate(await self._request_json("GET", "/health"))

    async def ready(self) -> ReadinessStatus:
        return ReadinessStatus.model_validate(await self._request_json("GET", "/ready", allowed_statuses={503}))

    async def metrics(self) -> str:
        return (await self._request("GET", "/metrics")).text

    # ------------------------------------------------------------- http core

    async def _authorization(self) -> str | None:
        auth = self._auth
        if auth is None:
            return None
        native = getattr(auth, "authorization_header_async", None)
        if native is not None:
            return await native()
        if isinstance(auth, TokenAuth):
            return auth.authorization_header()
        return await asyncio.to_thread(auth.authorization_header)

    async def _headers(self, extra: dict[str, str] | None) -> dict[str, str]:
        headers: dict[str, str] = {}
        value = await self._authorization()
        if value is not None:
            headers["Authorization"] = value
        if extra:
            headers.update(extra)
        return headers

    def _record(self, response: httpx.Response) -> None:
        self.last_response_info = ResponseInfo(
            request_id=response.headers.get("X-Request-ID"),
            rate_limit=RateLimitInfo.from_headers(response.headers),
        )

    async def _request(
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
        retryable_write = method.upper() == "POST" and bool(headers and headers.get("Idempotency-Key"))
        attempt = 0
        while True:
            attempt += 1
            try:
                response = await self._http.request(
                    method, path, params=params, json=json_body,
                    headers=await self._headers(headers),
                    **({"timeout": timeout} if timeout is not None else {}),
                )
            except httpx.TransportError as exc:
                if policy.can_retry(method, attempt) or (retryable_write and attempt < policy.max_attempts):
                    await self._sleeper(policy.delay(attempt))
                    continue
                raise NetworkError(f"{method} {path} failed before a response: {exc}") from exc
            self._record(response)
            keyed_status_retry = retryable_write and response.status_code != 429 and attempt < policy.max_attempts
            if (
                response.status_code >= 400
                and policy.is_retryable_status(response.status_code)
                and (policy.can_retry(method, attempt) or keyed_status_retry)
            ):
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                await self._sleeper(policy.delay(attempt, retry_after))
                continue
            if response.status_code >= 400 and response.status_code not in (allowed_statuses or set()):
                raise _map_error(response)
            return response

    async def _request_json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._request(method, path, **kwargs)
        try:
            return response.json()
        except ValueError as exc:
            raise ApiError(
                f"{method} {path} returned a non-JSON {response.status_code} response",
                status_code=response.status_code,
                request_id=response.headers.get("X-Request-ID"),
            ) from exc

    @asynccontextmanager
    async def _stream(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> AsyncIterator[httpx.Response]:
        async with self._http.stream(
            method, path, params=params, headers=await self._headers(headers),
            timeout=timeout or httpx.Timeout(DEFAULT_STREAM_READ_TIMEOUT, connect=5.0),
        ) as response:
            self._record(response)
            if response.status_code >= 400:
                await response.aread()
                raise _map_error(response)
            yield response


# ----------------------------------------------------------------- resources


class AsyncRunsResource:
    def __init__(self, client: AsyncMeemeeClient) -> None:
        self._client = client

    async def page(self, *, cursor: str | None = None, before: str | None = None, limit: int = 100) -> tuple[list[RunReport], str | None]:
        params: dict[str, Any] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        if before is not None:
            params["before"] = before
        payload = await self._client._request_json("GET", "/v1/runs", params=params)
        return [RunReport.model_validate(item) for item in payload["runs"]], payload.get("next_cursor")

    async def list(self, *, before: str | None = None, limit: int = 100) -> list[RunReport]:
        return (await self.page(before=before, limit=limit))[0]

    async def iter_all(self, *, limit: int = 100) -> AsyncIterator[RunReport]:
        cursor = None
        while True:
            items, cursor = await self.page(cursor=cursor, limit=limit)
            for item in items:
                yield item
            if cursor is None:
                return

    async def get(self, run_id: str) -> RunReport:
        return RunReport.model_validate(await self._client._request_json("GET", f"/v1/runs/{run_id}"))

    async def create(self, goal: str, *, approve_writes: bool = False, timeout: httpx.Timeout | float | None = None) -> RunReport:
        """POST /v1/runs - not auto-retried; a NetworkError can mean the run still executed."""
        _validate_goal(goal)
        payload = await self._client._request_json(
            "POST", "/v1/runs",
            json_body={"goal": goal, "approve_writes": approve_writes},
            timeout=timeout if timeout is not None else httpx.Timeout(DEFAULT_RUN_READ_TIMEOUT, connect=5.0),
        )
        return RunReport.model_validate(payload)


class AsyncJobsResource:
    def __init__(self, client: AsyncMeemeeClient) -> None:
        self._client = client

    async def create(self, goal: str, *, run_at: datetime | str | None = None, idempotency_key: str | None = None) -> CreatedJob:
        _validate_goal(goal)
        if idempotency_key is not None and (not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200):
            raise ValueError("idempotency_key must contain 1-200 characters")
        body: dict[str, Any] = {"goal": goal}
        scheduled = _iso_or_none(run_at, "run_at")
        if scheduled is not None:
            body["run_at"] = scheduled
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return CreatedJob.model_validate(await self._client._request_json("POST", "/v1/jobs", json_body=body, headers=headers))

    async def page(self, *, status: str | None = None, cursor: str | None = None, before: str | None = None, limit: int = 100) -> tuple[list[Job], str | None]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be 1-500")
        params: dict[str, Any] = {"limit": limit}
        if status is not None:
            params["status"] = status
        if cursor is not None:
            params["cursor"] = cursor
        if before is not None:
            params["before"] = before
        payload = await self._client._request_json("GET", "/v1/jobs", params=params)
        return [Job.model_validate(item) for item in payload["jobs"]], payload.get("next_cursor")

    async def list(self, *, status: str | None = None, before: str | None = None, limit: int = 100) -> list[Job]:
        return (await self.page(status=status, before=before, limit=limit))[0]

    async def iter_all(self, *, status: str | None = None, limit: int = 100) -> AsyncIterator[Job]:
        cursor = None
        while True:
            items, cursor = await self.page(status=status, cursor=cursor, limit=limit)
            for item in items:
                yield item
            if cursor is None:
                return

    async def get(self, job_id: str) -> Job:
        return Job.model_validate(await self._client._request_json("GET", f"/v1/jobs/{job_id}"))

    async def cancel(self, job_id: str) -> JobCancelResult:
        return JobCancelResult.model_validate(await self._client._request_json("DELETE", f"/v1/jobs/{job_id}"))

    async def events(self, job_id: str, *, after: int = 0) -> list[JobEvent]:
        payload = await self._client._request_json("GET", f"/v1/jobs/{job_id}/events", params={"after": after})
        return [JobEvent.model_validate(item) for item in payload["events"]]

    async def iter_events(self, job_id: str, *, after: int = 0) -> AsyncIterator[JobEvent]:
        cursor = after
        while True:
            batch = await self.events(job_id, after=cursor)
            if not batch:
                return
            for event in batch:
                cursor = max(cursor, event.sequence)
                yield event

    async def stream_events(
        self,
        job_id: str,
        *,
        after: int = 0,
        reconnect: bool = True,
        max_reconnects: int = 6,
        read_timeout: float = DEFAULT_STREAM_READ_TIMEOUT,
        on_heartbeat: Callable[[str], None] | None = None,
    ) -> AsyncIterator[JobEvent]:
        """SSE with Last-Event-ID resume; same contract as MeemeeClient.jobs.stream_events."""
        cursor = max(after, 0)
        reconnects_left = max_reconnects
        backoff = 1.0
        while True:
            headers = {"Accept": "text/event-stream"}
            if cursor:
                headers["Last-Event-ID"] = str(cursor)
            try:
                async with self._client._stream(
                    "GET", f"/v1/jobs/{job_id}/stream",
                    params={"after": cursor}, headers=headers,
                    timeout=httpx.Timeout(read_timeout, connect=5.0),
                ) as response:
                    parser = SSEParser()
                    async for chunk in response.aiter_bytes():
                        for message in parser.feed(chunk):
                            handled = JobsResource._handle_stream_message(message, job_id, on_heartbeat)
                            if handled is None:
                                continue
                            event, is_terminal = handled
                            cursor = max(cursor, event.sequence)
                            yield event
                            if is_terminal:
                                return
                    return
            except httpx.TransportError as exc:
                if not reconnect or reconnects_left <= 0:
                    raise NetworkError(
                        f"job stream for {job_id} interrupted and the reconnect budget is "
                        f"exhausted (last delivered event id {cursor}): {exc}"
                    ) from exc
                reconnects_left -= 1
                await self._client._sleeper(min(backoff, 30.0))
                backoff *= 2.0

    async def stream_ws(
        self,
        job_id: str,
        *,
        after: int = 0,
        reconnect: bool = True,
        max_reconnects: int = 6,
        open_timeout: float = 10.0,
        ping_interval: float | None = 20.0,
        ping_timeout: float | None = 20.0,
    ) -> AsyncIterator[JobEvent]:
        """WebSocket with ``?after`` resume; same contract as MeemeeClient.jobs.stream_ws."""
        websockets = _ws.require_websockets()
        from websockets.asyncio.client import connect
        from websockets.exceptions import ConnectionClosed, InvalidStatus

        cursor = max(after, 0)
        reconnects_left = max_reconnects
        backoff = 1.0
        while True:
            failure: BaseException | None = None
            try:
                async with connect(
                    _ws.ws_url(self._client._base_url, job_id, cursor),
                    additional_headers=await self._client._headers(None),
                    user_agent_header=self._client._user_agent,
                    open_timeout=open_timeout,
                    ping_interval=ping_interval,
                    ping_timeout=ping_timeout,
                    proxy=None,
                ) as connection:
                    while True:
                        try:
                            frame = await connection.recv()
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
                mapped = _ws.handshake_error(exc.response.status_code, job_id)
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
            await self._client._sleeper(min(backoff, 30.0))
            backoff *= 2.0

    async def wait(self, job_id: str, *, timeout: float | None = None, poll_interval: float = 2.0) -> Job:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            job = await self.get(job_id)
            if job.is_terminal:
                return job
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WaitTimeoutError(f"job {job_id} is still {job.status.value} after {timeout} seconds")
                await self._client._sleeper(min(poll_interval, remaining))
            else:
                await self._client._sleeper(poll_interval)


class AsyncTokensResource:
    def __init__(self, client: AsyncMeemeeClient) -> None:
        self._client = client

    async def create(self, name: str, scopes: set[str] | frozenset[str] | list[str], *, expires_at: datetime | str | None = None) -> CreatedToken:
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
        return CreatedToken.model_validate(await self._client._request_json("POST", "/v1/tokens", json_body=body))

    async def page(self, *, revoked: bool | None = None, cursor: str | None = None, before: str | None = None, limit: int = 100) -> tuple[list[TokenMetadata], str | None]:
        params: dict[str, Any] = {"limit": limit}
        if revoked is not None:
            params["revoked"] = revoked
        if cursor is not None:
            params["cursor"] = cursor
        if before is not None:
            params["before"] = before
        payload = await self._client._request_json("GET", "/v1/tokens", params=params)
        return [TokenMetadata.model_validate(item) for item in payload["tokens"]], payload.get("next_cursor")

    async def list(self, *, revoked: bool | None = None, before: str | None = None, limit: int = 100) -> list[TokenMetadata]:
        return (await self.page(revoked=revoked, before=before, limit=limit))[0]

    async def iter_all(self, *, revoked: bool | None = None, limit: int = 100) -> AsyncIterator[TokenMetadata]:
        cursor = None
        while True:
            items, cursor = await self.page(revoked=revoked, cursor=cursor, limit=limit)
            for item in items:
                yield item
            if cursor is None:
                return

    async def revoke(self, token_id: str) -> RevokedToken:
        return RevokedToken.model_validate(await self._client._request_json("DELETE", f"/v1/tokens/{token_id}"))


class AsyncAuditResource:
    def __init__(self, client: AsyncMeemeeClient) -> None:
        self._client = client

    async def page(self, *, after: int = 0, limit: int = 100) -> tuple[AuditPage, str | None]:
        if not (1 <= limit <= 500):
            raise ValueError("limit must be 1-500 (the server caps at 500)")
        payload = await self._client._request_json("GET", "/v1/audit", params={"after": after, "limit": limit})
        return AuditPage.model_validate(payload), payload.get("next_cursor")

    async def list(self, *, after: int = 0, limit: int = 100) -> AuditPage:
        return (await self.page(after=after, limit=limit))[0]

    async def iter_entries(self, *, after: int = 0, limit: int = 100) -> AsyncIterator[AuditEntry]:
        cursor = after
        while True:
            page = await self.list(after=cursor, limit=limit)
            if not page.entries:
                return
            for entry in page.entries:
                cursor = max(cursor, entry.sequence)
                yield entry


class AsyncQuotaResource:
    def __init__(self, client: AsyncMeemeeClient) -> None:
        self._client = client

    async def get(self) -> QuotaStatus:
        return QuotaStatus.model_validate(await self._client._request_json("GET", "/v1/quota"))

    async def set(self, principal_id: str, daily_jobs: int) -> QuotaStatus:
        if not 1 <= daily_jobs <= 1_000_000:
            raise ValueError("daily_jobs must be 1-1000000")
        payload = await self._client._request_json("PUT", f"/v1/quota/{principal_id}", json_body={"daily_jobs": daily_jobs})
        return QuotaStatus.model_validate(payload)


class AsyncApprovalsResource:
    def __init__(self, client: AsyncMeemeeClient) -> None:
        self._client = client

    async def list(self, principal_id: str) -> list[Approval]:
        payload = await self._client._request_json("GET", f"/v1/approvals/{_quote_segment(principal_id)}")
        return [Approval.model_validate(item) for item in payload["approvals"]]

    async def grant(self, principal_id: str, tool: str, expires_at: datetime | str | None = None, *, argument_constraints: dict[str, Any] | None = None) -> dict:
        if argument_constraints is not None and not isinstance(argument_constraints, dict):
            raise TypeError("argument_constraints must be a dict or None")
        body = {"tool": tool, "expires_at": _iso_or_none(expires_at, "expires_at"), "argument_constraints": argument_constraints}
        return await self._client._request_json("PUT", f"/v1/approvals/{_quote_segment(principal_id)}", json_body=body)

    async def revoke(self, principal_id: str, tool: str) -> dict:
        return await self._client._request_json(
            "DELETE", f"/v1/approvals/{_quote_segment(principal_id)}/{_quote_segment(tool)}"
        )


class AsyncWebhooksResource:
    def __init__(self, client: AsyncMeemeeClient) -> None:
        self._client = client

    async def create(self, url: str, events: set[str], *, fields: set[str] | None = None, headers: dict[str, str] | None = None) -> CreatedWebhook:
        if not events:
            raise ValueError("at least one event is required")
        payload = await self._client._request_json("POST", "/v1/webhooks", json_body={
            "url": url, "events": sorted(events), "fields": sorted(fields or ()), "headers": headers or {},
        })
        return CreatedWebhook.model_validate(payload)

    async def list(self) -> list[WebhookSubscription]:
        payload = await self._client._request_json("GET", "/v1/webhooks")
        return [WebhookSubscription.model_validate(item) for item in payload["webhooks"]]

    async def delete(self, webhook_id: str) -> dict:
        return await self._client._request_json("DELETE", f"/v1/webhooks/{webhook_id}")

    async def rotate_secret(self, webhook_id: str) -> CreatedWebhook:
        return CreatedWebhook.model_validate(await self._client._request_json("POST", f"/v1/webhooks/{webhook_id}/rotate-secret"))

    async def deliveries_page(self, *, status: str | None = None, after: str | None = None, cursor: str | None = None, limit: int = 100) -> tuple[list[WebhookDelivery], str | None]:
        params: dict[str, Any] = {"limit": limit}
        if status is not None:
            params["status"] = status
        if after is not None:
            params["after"] = after
        if cursor is not None:
            params["cursor"] = cursor
        payload = await self._client._request_json("GET", "/v1/webhook-deliveries", params=params)
        return [WebhookDelivery.model_validate(item) for item in payload["deliveries"]], payload.get("next_cursor")

    async def deliveries(self, *, status: str | None = None, after: str | None = None, limit: int = 100) -> list[WebhookDelivery]:
        return (await self.deliveries_page(status=status, after=after, limit=limit))[0]

    async def iter_deliveries(self, *, status: str | None = None, limit: int = 100) -> AsyncIterator[WebhookDelivery]:
        cursor = None
        while True:
            items, cursor = await self.deliveries_page(status=status, cursor=cursor, limit=limit)
            for item in items:
                yield item
            if cursor is None:
                return

    async def replay(self, delivery_id: str) -> dict:
        return await self._client._request_json("POST", f"/v1/webhook-deliveries/{delivery_id}/replay")


class AsyncCompanionResource:
    def __init__(self, client: AsyncMeemeeClient) -> None:
        self._client = client

    async def list_users(self, *, limit: int = 100) -> list[CompanionUser]:
        payload = await self._client._request_json("GET", "/v1/companion/users", params={"limit": limit})
        return [CompanionUser.model_validate(item) for item in payload["users"]]

    async def upsert_user(self, user_id: str, display_name: str, *, timezone: str = "UTC", persona: PersonaConfig | None = None, checkins: CheckInPreferences | None = None) -> CompanionUser:
        body: dict[str, Any] = {"display_name": display_name, "timezone": timezone}
        if persona is not None:
            body["persona"] = persona.model_dump()
        if checkins is not None:
            body["checkins"] = checkins.model_dump()
        return CompanionUser.model_validate(await self._client._request_json("PUT", f"/v1/companion/users/{user_id}", json_body=body))

    async def get_user(self, user_id: str) -> CompanionUser:
        return CompanionUser.model_validate(await self._client._request_json("GET", f"/v1/companion/users/{user_id}"))

    async def update_persona(self, user_id: str, persona: PersonaConfig) -> CompanionUser:
        payload = await self._client._request_json(
            "PUT", f"/v1/companion/users/{user_id}/persona", json_body={"persona": persona.model_dump()}
        )
        return CompanionUser.model_validate(payload)

    async def update_checkins(self, user_id: str, checkins: CheckInPreferences) -> CheckInUpdateResult:
        payload = await self._client._request_json(
            "PUT", f"/v1/companion/users/{user_id}/checkins", json_body={"checkins": checkins.model_dump()}
        )
        return CheckInUpdateResult.model_validate(payload)

    async def list_facts(self, user_id: str, *, query: str | None = None, limit: int = 200) -> list[CompanionFact]:
        params: dict[str, Any] = {"limit": limit}
        if query is not None:
            params["query"] = query
        payload = await self._client._request_json("GET", f"/v1/companion/users/{user_id}/facts", params=params)
        return [CompanionFact.model_validate(item) for item in payload["facts"]]

    async def add_fact(self, user_id: str, text: str, *, category: str = "general", confidence: float = 1.0) -> CompanionFact:
        payload = await self._client._request_json(
            "POST", f"/v1/companion/users/{user_id}/facts",
            json_body={"category": category, "text": text, "confidence": confidence},
        )
        return CompanionFact.model_validate(payload)

    async def retire_fact(self, user_id: str, fact_id: int) -> FactRetireResult:
        return FactRetireResult.model_validate(
            await self._client._request_json("DELETE", f"/v1/companion/users/{user_id}/facts/{fact_id}")
        )

    async def chat(self, user_id: str, text: str, *, channel: str = "local", conversation_id: str | None = None, timeout: httpx.Timeout | float | None = None) -> CompanionChatReply:
        """POST /v1/companion/chat - not auto-retried; a network failure can mean the turn executed."""
        if not text.strip():
            raise ValueError("text must not be empty")
        body: dict[str, Any] = {"user_id": user_id, "text": text, "channel": channel}
        if conversation_id is not None:
            body["conversation_id"] = conversation_id
        payload = await self._client._request_json(
            "POST", "/v1/companion/chat", json_body=body,
            timeout=timeout if timeout is not None else httpx.Timeout(300.0, connect=5.0),
        )
        return CompanionChatReply.model_validate(payload)

    async def list_conversations(self, user_id: str, *, limit: int = 50) -> list[CompanionConversation]:
        payload = await self._client._request_json("GET", f"/v1/companion/users/{user_id}/conversations", params={"limit": limit})
        return [CompanionConversation.model_validate(item) for item in payload["conversations"]]

    async def messages(self, conversation_id: str, *, limit: int = 100) -> list[CompanionMessage]:
        payload = await self._client._request_json(
            "GET", f"/v1/companion/conversations/{conversation_id}/messages", params={"limit": limit}
        )
        return [CompanionMessage.model_validate(item) for item in payload["messages"]]

    async def plan_checkin(self, user_id: str) -> CompanionCheckIn:
        return CompanionCheckIn.model_validate(await self._client._request_json("POST", f"/v1/companion/users/{user_id}/checkins/plan"))

    async def list_checkins(self, user_id: str, *, status: str | None = None, limit: int = 50) -> list[CompanionCheckIn]:
        params: dict[str, Any] = {"limit": limit}
        if status is not None:
            params["status"] = status
        payload = await self._client._request_json("GET", f"/v1/companion/users/{user_id}/checkins", params=params)
        return [CompanionCheckIn.model_validate(item) for item in payload["checkins"]]

    async def tick(self) -> CheckInTickResult:
        return CheckInTickResult.model_validate(await self._client._request_json("POST", "/v1/companion/checkins/tick"))
