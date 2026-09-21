"""Error hierarchy mapping the Meemee API's failure surface to typed exceptions.

Server contract (meemee v0.16.0):
- Error bodies are JSON with a ``detail`` key: a string for HTTPException, or a
  list of ``{loc, msg, type}`` objects for FastAPI request-validation failures.
- Every response carries ``X-Request-ID``; 500 responses add ``request_id`` in the body.
- 401 adds ``WWW-Authenticate: Bearer``; 403 detail is ``missing scope: <scope>``.
- 429 adds ``Retry-After`` plus ``RateLimit-Limit/Remaining/Reset`` headers.
"""
from __future__ import annotations

from typing import Any


class MeemeeError(Exception):
    """Base class for every error raised by the SDK."""


class ApiError(MeemeeError):
    """The server answered with an HTTP error status."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        request_id: str | None = None,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.request_id = request_id
        #: Raw ``detail`` value from the response body (str, list, or None).
        self.detail = detail

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"{type(self).__name__}(status_code={self.status_code}, request_id={self.request_id!r}, detail={self.detail!r})"


class BadRequestError(ApiError):
    """HTTP 400 - the request itself was malformed (e.g. a non-integer Last-Event-ID)."""


class AuthenticationError(ApiError):
    """HTTP 401 - missing or invalid bearer token, or a failed OIDC token request."""

    def __init__(self, message: str, *, status_code: int = 401, request_id: str | None = None,
                 detail: Any = None, www_authenticate: str | None = None) -> None:
        super().__init__(message, status_code=status_code, request_id=request_id, detail=detail)
        self.www_authenticate = www_authenticate


class PermissionDeniedError(ApiError):
    """HTTP 403 - the token is valid but lacks the required scope."""

    def __init__(self, message: str, *, status_code: int = 403, request_id: str | None = None,
                 detail: Any = None, missing_scope: str | None = None) -> None:
        super().__init__(message, status_code=status_code, request_id=request_id, detail=detail)
        self.missing_scope = missing_scope


class NotFoundError(ApiError):
    """HTTP 404 - the addressed job or token does not exist."""


class ConflictError(ApiError):
    """HTTP 409 - state conflict, e.g. cancelling a job that is already finished."""


class ValidationError(ApiError):
    """HTTP 422 - request body failed validation, or a parameter was not ISO 8601."""

    def __init__(self, message: str, *, status_code: int = 422, request_id: str | None = None,
                 detail: Any = None, issues: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message, status_code=status_code, request_id=request_id, detail=detail)
        #: FastAPI validation issue list when the server returned one, else None.
        self.issues = issues


class RateLimitError(ApiError):
    """HTTP 429 - fixed-window rate limit exceeded."""

    def __init__(self, message: str, *, status_code: int = 429, request_id: str | None = None,
                 detail: Any = None, retry_after: float | None = None) -> None:
        super().__init__(message, status_code=status_code, request_id=request_id, detail=detail)
        #: Seconds the server asked the client to wait (from Retry-After), else None.
        self.retry_after = retry_after


class ServerError(ApiError):
    """HTTP 5xx - the server failed to complete a valid request."""


class NetworkError(MeemeeError):
    """The request never received a response: connect/read/write failures and timeouts."""


class StreamError(MeemeeError):
    """The SSE stream failed: a server ``error`` frame, a malformed frame, or an
    unrecoverable interruption after the reconnect budget was exhausted."""


class WaitTimeoutError(MeemeeError):
    """``jobs.wait()`` exceeded its deadline before the job reached a terminal state."""
