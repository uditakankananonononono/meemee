"""Shared WebSocket helpers for job-event streaming (sync and asyncio).

Server contract (GET /v1/jobs/{id}/ws, server v0.117.0):
- Bearer auth in the ``Authorization`` handshake header (API token, bootstrap
  token or OIDC access token); ``jobs:read`` or ``admin`` scope required.
- ``?after=N`` resumes after event sequence N; each text frame is one JSON
  JobEvent, in sequence order.
- The server closes with 1000 once the job is terminal and every stored event
  has been sent. Rejections close with 4400 (bad cursor), 4401 (no valid
  credential), 4403 (missing jobs:read) or 4404 (unknown or foreign job).
- Older servers and some proxies reject before the handshake completes with a
  plain HTTP status instead; that status is mapped the same way.

The transport is the optional ``websockets`` package (``pip install
'meemee-client[ws]'``); it is imported lazily so the core SDK keeps its three
dependencies.
"""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from .errors import (
    ApiError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    StreamError,
)
from .models import TERMINAL_EVENT_KINDS, JobEvent

#: Close codes that mean "do not reconnect, raise instead".
REJECTION_CODES = {4400, 4401, 4403, 4404}
NORMAL_CLOSE = 1000


def require_websockets() -> Any:
    try:
        import websockets
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "WebSocket streaming needs the optional 'websockets' package: "
            "pip install 'meemee-client[ws]'"
        ) from exc
    return websockets


def ws_url(base_url: str, job_id: str, after: int) -> str:
    if base_url.startswith("https://"):
        root = "wss://" + base_url[len("https://"):]
    elif base_url.startswith("http://"):
        root = "ws://" + base_url[len("http://"):]
    else:  # pragma: no cover - the client constructor already validates this
        raise ValueError("base_url must be an absolute http(s) URL")
    return f"{root.rstrip('/')}/v1/jobs/{job_id}/ws?{urlencode({'after': max(after, 0)})}"


def rejection_error(code: int | None, reason: str, job_id: str) -> ApiError | None:
    """Map a server rejection close code to the matching typed API error."""
    message = f"WebSocket for job {job_id} closed with {code}: {reason or 'no reason given'}"
    if code == 4400:
        return BadRequestError(message, status_code=400, detail=reason)
    if code == 4401:
        return AuthenticationError(message, status_code=401, detail=reason)
    if code == 4403:
        return PermissionDeniedError(message, status_code=403, detail=reason, missing_scope="jobs:read")
    if code == 4404:
        return NotFoundError(message, status_code=404, detail=reason)
    return None


def handshake_error(status: int, job_id: str) -> ApiError | None:
    """Map a plain HTTP handshake rejection. Returns None for retryable 5xx/other."""
    message = f"WebSocket handshake for job {job_id} was rejected with HTTP {status}"
    if status == 400:
        return BadRequestError(message, status_code=400)
    if status == 401:
        return AuthenticationError(message, status_code=401)
    if status == 403:
        # Pre-accept rejections all look like 403: bad credential, missing
        # scope or an unknown job are indistinguishable at this layer.
        return PermissionDeniedError(message + " (credential, scope or job ownership)", status_code=403)
    if status == 404:
        return NotFoundError(message, status_code=404)
    if 400 <= status < 500 and status not in (408, 429):
        return ApiError(message, status_code=status)
    return None


def parse_frame(frame: str | bytes, job_id: str) -> tuple[JobEvent, bool]:
    """Decode one text frame into a JobEvent and whether it is terminal."""
    if isinstance(frame, bytes):
        try:
            frame = frame.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise StreamError(f"non-UTF-8 WebSocket frame on job {job_id}") from exc
    try:
        data = json.loads(frame)
    except ValueError as exc:
        raise StreamError(f"malformed WebSocket frame on job {job_id}: {frame[:200]!r}") from exc
    try:
        event = JobEvent.model_validate(data)
    except ValueError as exc:
        raise StreamError(f"unparseable job event on job {job_id}: {data!r}") from exc
    return event, event.kind in TERMINAL_EVENT_KINDS
