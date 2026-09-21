from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    """In-process sliding-window limiter. Use an external shared limiter for multi-pod deployments."""

    def __init__(self, app, requests: int = 60, window_seconds: int = 60):
        super().__init__(app)
        self.requests = requests
        self.window = window_seconds
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.lock = asyncio.Lock()

    async def dispatch(self, request: Request, call_next):
        key = request.client.host if request.client else "unknown"
        now = time.monotonic()
        async with self.lock:
            hits = self.hits[key]
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.requests:
                return JSONResponse({"detail": "rate limit exceeded"}, status_code=429, headers={"Retry-After": str(self.window)})
            hits.append(now)
        return await call_next(request)
