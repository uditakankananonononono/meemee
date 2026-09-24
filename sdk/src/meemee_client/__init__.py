"""meemee-client: typed Python SDK for the Meemee agent platform API.

Targets the Meemee server v0.122.0 HTTP contract: scoped API tokens and OIDC
bearer auth, synchronous runs, durable queued jobs, resume-safe SSE progress,
fixed-window rate limiting, and the tamper-evident audit chain. An asyncio
client (AsyncMeemeeClient) mirrors the whole surface, and job progress streams
over SSE or, with the ``ws`` extra, WebSocket.

Quick start:
    from meemee_client import MeemeeClient

    with MeemeeClient("http://127.0.0.1:8787", auth="mee_...") as client:
        job = client.jobs.create("Summarise today's arXiv cs.AI highlights")
        for event in client.jobs.stream_events(job.id):
            print(event.kind, event.payload)
"""
from ._version import __version__
from .auth import AsyncOIDCClientCredentialsAuth, AuthProvider, OIDCClientCredentialsAuth, TokenAuth
from .aio import AsyncMeemeeClient
from .client import MeemeeClient
from .errors import (
    ApiError,
    AuthenticationError,
    BadRequestError,
    ConflictError,
    IdempotencyConflictError,
    MeemeeError,
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
    TERMINAL_JOB_STATUSES,
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
    JobQuotaSnapshot,
    JobStatus,
    PersonaConfig,
    QuietHours,
    QuotaStatus,
    RateLimitInfo,
    ReadinessComponent,
    ReadinessStatus,
    ResponseInfo,
    RevokedToken,
    TokenIntrospection,
    TokenMetadata,
    RunReport,
    WebhookDelivery,
    WebhookSubscription,
)
from .retry import RetryPolicy
from .sse import SSEMessage, SSEParser

__all__ = [
    "KNOWN_SCOPES",
    "TERMINAL_EVENT_KINDS",
    "TERMINAL_JOB_STATUSES",
    "ApiError",
    "Approval",
    "AuditEntry",
    "AuditPage",
    "CheckInPreferences",
    "CheckInTickResult",
    "CheckInUpdateResult",
    "CompanionCheckIn",
    "CompanionChatReply",
    "CompanionConversation",
    "CompanionFact",
    "CompanionMessage",
    "CompanionUser",
    "FactRetireResult",
    "PersonaConfig",
    "QuietHours",
    "AuthProvider",
    "AuthenticationError",
    "BadRequestError",
    "ConflictError",
    "IdempotencyConflictError",
    "CreatedJob",
    "CreatedToken",
    "CreatedWebhook",
    "HealthStatus",
    "Job",
    "JobCancelResult",
    "JobEvent",
    "JobQuotaSnapshot",
    "JobStatus",
    "MeemeeClient",
    "AsyncMeemeeClient",
    "MeemeeError",
    "NetworkError",
    "NotFoundError",
    "OIDCClientCredentialsAuth",
    "AsyncOIDCClientCredentialsAuth",
    "PermissionDeniedError",
    "QuotaStatus",
    "RateLimitError",
    "RateLimitInfo",
    "ReadinessComponent",
    "ReadinessStatus",
    "ResponseInfo",
    "RetryPolicy",
    "RevokedToken",
    "RunReport",
    "SSEMessage",
    "SSEParser",
    "ServerError",
    "StreamError",
    "TokenAuth",
    "TokenIntrospection",
    "TokenMetadata",
    "ValidationError",
    "WaitTimeoutError",
    "WebhookDelivery",
    "WebhookSubscription",
    "__version__",
]
