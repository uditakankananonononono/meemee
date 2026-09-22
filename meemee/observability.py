from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

HTTP_REQUESTS = Counter("meemee_http_requests_total", "HTTP requests", ("method", "route", "status"))
HTTP_DURATION = Histogram("meemee_http_request_duration_seconds", "HTTP request duration", ("method", "route"))
HTTP_INFLIGHT = Gauge("meemee_http_requests_inflight", "In-flight HTTP requests")
AGENT_RUNS = Counter("meemee_agent_runs_total", "Agent runs", ("outcome",))
JOBS_CREATED = Counter("meemee_jobs_created_total", "Queued jobs created")
WEBHOOK_OUTBOX = Gauge("meemee_webhook_deliveries", "Webhook outbox rows", ("status",))


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        started = time.perf_counter()
        HTTP_INFLIGHT.inc()
        status = "500"
        try:
            response = await call_next(request)
            status = str(response.status_code)
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", request.url.path)
            HTTP_INFLIGHT.dec()
            HTTP_REQUESTS.labels(request.method, route, status).inc()
            HTTP_DURATION.labels(request.method, route).observe(time.perf_counter() - started)


def metrics_response() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


class JSONFormatter(logging.Formatter):
    """Stable JSON logs with exception rendering and request correlation fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in ("request_id", "actor_id", "resource"):
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def configure_logging(level: str, json_logs: bool = True) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter() if json_logs else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
