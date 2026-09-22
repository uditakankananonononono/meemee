"""meemee-client: typed Python SDK for the Meemee agent platform API.

Targets the Meemee server v0.98.0 HTTP contract: scoped API tokens and OIDC
bearer auth, synchronous runs, durable queued jobs, resume-safe SSE progress,
fixed-window rate limiting, and the tamper-evident audit chain.

Quick start:
    from meemee_client import MeemeeClient

    with MeemeeClient("http://127.0.0.1:8787", auth="mee_...") as client:
        job = client.jobs.create("Summarise today's arXiv cs.AI highlights")
        for event in client.jobs.stream_events(job.id):
            print(event.kind, event.payload)
"""
from ._version import __version__
from .auth import AuthProvider, OIDCClientCredentialsAuth, TokenAuth
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
    CreatedJob,
    CreatedToken,
    CreatedWebhook,
    HealthStatus,
    Job,
    JobCancelResult,
    JobEvent,
    JobStatus,
    QuotaStatus,
    RateLimitInfo,
    ReadinessStatus,
    ResponseInfo,
    RevokedToken,
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
    "JobStatus",
    "MeemeeClient",
    "MeemeeError",
    "NetworkError",
    "NotFoundError",
    "OIDCClientCredentialsAuth",
    "PermissionDeniedError",
    "QuotaStatus",
    "RateLimitError",
    "RateLimitInfo",
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
    "TokenMetadata",
    "ValidationError",
    "WaitTimeoutError",
    "WebhookDelivery",
    "WebhookSubscription",
    "__version__",
]
