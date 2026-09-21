"""meemee-client: typed Python SDK for the Meemee agent platform API.

Targets the Meemee server v0.16.0 HTTP contract: scoped API tokens and OIDC
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
    AuditEntry,
    AuditPage,
    CreatedJob,
    CreatedToken,
    HealthStatus,
    Job,
    JobCancelResult,
    JobEvent,
    JobStatus,
    KNOWN_SCOPES,
    RateLimitInfo,
    ReadinessStatus,
    ResponseInfo,
    RevokedToken,
    RunReport,
    TERMINAL_EVENT_KINDS,
    TERMINAL_JOB_STATUSES,
)
from .retry import RetryPolicy
from .sse import SSEMessage, SSEParser

__all__ = [
    "__version__",
    "MeemeeClient",
    "AuthProvider",
    "TokenAuth",
    "OIDCClientCredentialsAuth",
    "RetryPolicy",
    "SSEMessage",
    "SSEParser",
    "HealthStatus",
    "ReadinessStatus",
    "RunReport",
    "Job",
    "JobStatus",
    "CreatedJob",
    "JobCancelResult",
    "JobEvent",
    "CreatedToken",
    "RevokedToken",
    "AuditEntry",
    "AuditPage",
    "RateLimitInfo",
    "ResponseInfo",
    "KNOWN_SCOPES",
    "TERMINAL_EVENT_KINDS",
    "TERMINAL_JOB_STATUSES",
    "MeemeeError",
    "ApiError",
    "BadRequestError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "ConflictError",
    "ValidationError",
    "RateLimitError",
    "ServerError",
    "NetworkError",
    "StreamError",
    "WaitTimeoutError",
]
