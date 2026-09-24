"""Typed models for the current Meemee API contract."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthStatus(BaseModel):
    status: str
    version: str


class ReadinessComponent(BaseModel):
    model_config = ConfigDict(extra="allow")
    ok: bool
    error: str | None = None


class ReadinessStatus(BaseModel):
    status: str
    components: dict[str, ReadinessComponent] = Field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"

    def failing(self) -> list[str]:
        return sorted(name for name, detail in self.components.items() if not detail.ok)


class ApprovalRefusal(BaseModel):
    """A tool call the run was refused. Any refusal means the goal may not be done."""

    model_config = ConfigDict(extra="allow")

    step: int
    tool: str
    risk: str
    reason: Literal["approval_required", "policy_denied"]
    detail: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    grantable: bool
    per_run: dict[str, Any] | None = None
    """Body fields for a retried POST /v1/runs that would allow this call."""
    persistent_grant: dict[str, Any] | None = None
    """Body for PUT /v1/approvals/{principal} that would allow this call."""


class RunReport(BaseModel):
    """Result of a synchronous POST /v1/runs agent execution."""

    run_id: str
    goal: str
    final: str
    steps_used: int
    tool_results: list[dict[str, Any]] = Field(default_factory=list)
    approvals_required: list[ApprovalRefusal] = Field(default_factory=list)
    created_at: datetime | None = None

    @property
    def is_blocked(self) -> bool:
        """True when any tool call was refused; do not present such a run as done."""
        return bool(self.approvals_required)


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

    @property
    def approvals_required(self) -> list[ApprovalRefusal]:
        """Refused tool calls recorded in a finished job's run report ([] when none or no result)."""
        data = self.result_data
        if not isinstance(data, dict):
            return []
        return [ApprovalRefusal.model_validate(item) for item in data.get("approvals_required", [])]


class JobQuotaSnapshot(BaseModel):
    day: str
    limit: int
    used: int
    remaining: int


class CreatedJob(BaseModel):
    id: str
    quota: JobQuotaSnapshot | None = None


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
#: The server closes the SSE stream itself after done/failed; a cancelled job
#: keeps the stream open (heartbeats only), so the SDK closes it client-side.
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
KNOWN_SCOPES: frozenset[str] = frozenset({"admin", "runs:write", "jobs:read", "jobs:write", "companion:read", "companion:write"})


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
    def from_headers(cls, headers: Any) -> RateLimitInfo | None:
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

class QuotaStatus(BaseModel):
    day: str | None = None
    principal: str | None = None
    limit: int
    used: int
    remaining: int
    resets_at: datetime | None = None


class Approval(BaseModel):
    principal: str
    tool: str
    granted_by: str
    granted_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    argument_constraints: dict[str, Any] | None = None


class WebhookSubscription(BaseModel):
    id: str
    url: str
    events: str
    active: int
    created_at: datetime


class CreatedWebhook(BaseModel):
    id: str
    secret: str
    warning: str = ""


class WebhookDelivery(BaseModel):
    id: str
    subscription_id: str
    event_id: str
    event_type: str
    payload_sha256: str | None = None
    status: str
    attempts: int
    next_attempt_at: float
    response_status: int | None = None
    last_error: str | None = None
    created_at: datetime


class TokenMetadata(BaseModel):
    id: str
    name: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None


class PersonaConfig(BaseModel):
    """How the companion speaks for and to one user."""

    display_name: str = "Meemee"
    tone: str = "warm, direct and honest"
    style_rules: list[str] = Field(default_factory=list)
    language: str = "en"
    use_emoji: bool = False
    custom_instructions: str = ""


class QuietHours(BaseModel):
    """Local HH:MM window during which proactive check-ins stay silent."""

    start: str
    end: str


class CheckInPreferences(BaseModel):
    enabled: bool = False
    cadence_minutes: int = 360
    quiet_hours: QuietHours | None = None
    channel: str = "local"
    address: str | None = None


class CompanionUser(BaseModel):
    user_id: str
    display_name: str
    timezone: str
    persona: PersonaConfig
    checkins: CheckInPreferences
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CompanionFact(BaseModel):
    id: int
    user_id: str
    category: str
    text: str
    confidence: float
    source: str
    superseded_by: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CompanionChatReply(BaseModel):
    conversation_id: str
    reply: str
    facts_learned: int
    persona: str
    model_trace: dict[str, Any] | None = None


class CompanionConversation(BaseModel):
    id: str
    user_id: str
    channel: str
    created_at: datetime | None = None
    last_message_at: datetime | None = None


class CompanionMessage(BaseModel):
    id: int
    conversation_id: str
    role: str
    content: str
    created_at: datetime | None = None


class CompanionCheckIn(BaseModel):
    id: str
    user_id: str
    slot: str
    due_at: datetime
    status: str
    attempts: int
    max_attempts: int
    channel: str
    address: str | None = None
    message: str | None = None
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CheckInUpdateResult(BaseModel):
    user: CompanionUser
    cancelled_pending: int


class FactRetireResult(BaseModel):
    fact_id: int
    active: bool


class CheckInTickResult(BaseModel):
    planned: int
    deliveries: list[dict[str, Any]] = Field(default_factory=list)
