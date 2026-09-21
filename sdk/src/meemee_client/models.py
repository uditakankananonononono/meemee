"""Typed models for the Meemee API (server v0.16.0 contract)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class HealthStatus(BaseModel):
    status: str
    version: str


class ReadinessComponent(BaseModel):
    """One component of the server's readiness report (server v0.20+).

    Known components: memory, jobs, tokens (databases), disk, model. Extra
    detail fields (free_bytes, status, error, ...) are preserved.
    """

    model_config = ConfigDict(extra="allow")

    ok: bool


class ReadinessReport(BaseModel):
    """GET /ready response. The server returns this body with 200 when ready
    and with 503 when not - both are parsed; check ``is_ready``/``failing``.

    On pre-v0.20 servers the body is only {"status": "ready"} and components
    is empty.
    """

    status: str
    components: dict[str, ReadinessComponent] = Field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"

    def failing(self) -> list[str]:
        """Names of components whose check failed."""
        return [name for name, component in self.components.items() if not component.ok]


#: Backwards-compatible alias from SDK 0.1.x.
ReadinessStatus = ReadinessReport


class RunReport(BaseModel):
    """Result of a synchronous POST /v1/runs agent execution."""

    run_id: str
    goal: str
    final: str
    steps_used: int
    tool_results: list[dict[str, Any]] = Field(default_factory=list)


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


#: States in which a job will never change again.
TERMINAL_JOB_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED}
)


class Job(BaseModel):
    """A durable queued job as returned by GET /v1/jobs/{id}.

    The server stores ``result`` as a JSON-encoded string; use ``result_data``
    for the decoded value.
    """

    model_config = ConfigDict(use_enum_values=False)

    id: str
    goal: str
    run_at: datetime
    status: JobStatus
    attempts: int
    max_attempts: int
    result: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_JOB_STATUSES

    @property
    def result_data(self) -> Any:
        """Decoded job result, or None when there is no stored result.

        Raises ValueError if the server returned a non-JSON result string -
        that would be a server contract violation, and hiding it helps nobody.
        """
        if self.result is None:
            return None
        return json.loads(self.result)


class QuotaStatus(BaseModel):
    """Per-principal daily job quota (server v0.20+). ``day`` is the UTC date."""

    day: str
    used: int
    limit: int
    remaining: int


class CreatedJob(BaseModel):
    id: str
    #: Quota snapshot after this create consumed one job (server v0.20+);
    #: None when talking to an older server.
    quota: QuotaStatus | None = None


class JobCancelResult(BaseModel):
    id: str
    status: JobStatus


class JobEvent(BaseModel):
    """One durable entry from a job's append-only event log.

    Event kinds emitted by the server: queued, running, retry, done, failed,
    cancel_requested, cancelled.
    """

    sequence: int
    job_id: str
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


#: Event kinds after which no further events can meaningfully advance the job.
#: Server v0.21.1+ closes the SSE stream after any of them; earlier servers
#: only closed after done/failed, so the SDK closes client-side to stay correct
#: against every version.
TERMINAL_EVENT_KINDS: frozenset[str] = frozenset({"done", "failed", "cancelled"})


class CreatedToken(BaseModel):
    """A newly minted API token. ``token`` is shown exactly once by the server."""

    id: str
    token: str
    warning: str = ""


class RevokedToken(BaseModel):
    id: str
    revoked: bool


#: The complete set of scopes the server accepts when minting tokens.
KNOWN_SCOPES: frozenset[str] = frozenset({"admin", "runs:write", "jobs:read", "jobs:write"})


class AuditEntry(BaseModel):
    sequence: int
    occurred_at: datetime
    actor_id: str
    action: str
    resource: str
    outcome: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    previous_hash: str
    entry_hash: str


class AuditPage(BaseModel):
    """One page of the tamper-evident audit chain.

    ``verified`` is False only when the server detected chain corruption - in
    which case it answers 500 instead, so a parsed page always has verified=True.
    """

    verified: bool
    entries: list[AuditEntry] = Field(default_factory=list)


class RateLimitInfo(BaseModel):
    """Snapshot of the server's fixed-window rate limiter from response headers."""

    limit: int | None = None
    remaining: int | None = None
    #: Window end as an aware datetime (server sends epoch seconds).
    reset: datetime | None = None

    @classmethod
    def from_headers(cls, headers: Any) -> "RateLimitInfo | None":
        raw_limit = headers.get("RateLimit-Limit")
        raw_remaining = headers.get("RateLimit-Remaining")
        raw_reset = headers.get("RateLimit-Reset")
        if raw_limit is None and raw_remaining is None and raw_reset is None:
            return None
        reset = None
        if raw_reset is not None:
            reset = datetime.fromtimestamp(int(raw_reset), tz=timezone.utc)
        return cls(
            limit=int(raw_limit) if raw_limit is not None else None,
            remaining=int(raw_remaining) if raw_remaining is not None else None,
            reset=reset,
        )


class ResponseInfo(BaseModel):
    """Metadata captured from the most recent response on a client."""

    request_id: str | None = None
    rate_limit: RateLimitInfo | None = None
