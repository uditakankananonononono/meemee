from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import __version__
from .audit import AuditLog
from .auth import Authenticator, TokenStore
from .config import Settings
from .idempotency import IdempotencyConflict, IdempotencyStore
from .jobs import JobStore
from .observability import (
    AGENT_RUNS,
    JOBS_CREATED,
    MetricsMiddleware,
    configure_logging,
    metrics_response,
)
from .oidc import OIDCConfig, OIDCValidator
from .rate_limit import RateLimitMiddleware, SQLiteRateLimiter
from .runtime import build_agent
from .streaming import job_event_stream
from .web_login import WebLogin, WebLoginConfig

settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
configure_logging(settings.log_level, settings.log_json)
log = logging.getLogger("meemee.api")
app = FastAPI(title="Meemee", version=__version__)
app.add_middleware(MetricsMiddleware)
app.add_middleware(RateLimitMiddleware, limiter=SQLiteRateLimiter(settings.data_dir / "rate-limits.sqlite3", settings.rate_limit_requests, settings.rate_limit_window_seconds))
agent = build_agent(settings)
jobs = JobStore(settings.data_dir / "jobs.sqlite3")
idempotency = IdempotencyStore(settings.data_dir / "idempotency.sqlite3")
tokens = TokenStore(settings.data_dir / "auth.sqlite3")
audit = AuditLog(settings.data_dir / "audit.sqlite3")
oidc = None
if any((settings.oidc_issuer, settings.oidc_audience, settings.oidc_jwks_url)):
    if not all((settings.oidc_issuer, settings.oidc_audience, settings.oidc_jwks_url)):
        raise RuntimeError("OIDC requires issuer, audience and JWKS URL together")
    oidc = OIDCValidator(OIDCConfig(
        settings.oidc_issuer, settings.oidc_audience, settings.oidc_jwks_url,
        settings.oidc_role_claim, settings.oidc_role_scopes,
    ))
web_login = None
web_values = (settings.oidc_client_id, settings.oidc_client_secret, settings.oidc_authorization_endpoint, settings.oidc_token_endpoint, settings.oidc_redirect_uri, settings.session_key)
if any(web_values):
    if oidc is None or not all(web_values):
        raise RuntimeError("interactive login requires complete OIDC and web-login configuration")
    web_login = WebLogin(WebLoginConfig(*web_values), oidc)
auth = Authenticator(tokens, settings.api_token, oidc, web_login.authenticate_session if web_login else None)
jobs_write_auth = auth.dependency("jobs:write")
jobs_write_dependency = Depends(jobs_write_auth)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    try:
        response = await call_next(request)
    except Exception:
        log.exception("unhandled request error", extra={"request_id": request_id})
        response = JSONResponse({"detail": "internal server error", "request_id": request_id}, 500)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


class RunRequest(BaseModel):
    goal: str = Field(min_length=2, max_length=20_000)
    approve_writes: bool = False


class JobRequest(BaseModel):
    goal: str = Field(min_length=2, max_length=20_000)
    run_at: str | None = None


class TokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: set[str] = Field(min_length=1)
    expires_at: str | None = None


@app.get("/health")
def health():
    return {"status": "ok", "version": __version__}


@app.get("/ready")
def ready():
    try:
        agent.memory.connection.execute("SELECT 1").fetchone()
        jobs.db.execute("SELECT 1").fetchone()
    except sqlite3.Error as exc:
        raise HTTPException(503, "database unavailable") from exc
    return {"status": "ready"}


@app.post("/v1/runs", dependencies=[Depends(auth.dependency("runs:write"))])
async def create_run(request: RunRequest):
    try:
        report = await agent.run(request.goal, approve=lambda *_: request.approve_writes)
        audit.append("api", "run.create", report.run_id, "success", {"steps": report.steps_used})
        AGENT_RUNS.labels("success").inc()
        return report
    except (OSError, ValueError, RuntimeError) as exc:
        AGENT_RUNS.labels("failed").inc()
        raise HTTPException(status_code=502, detail=f"agent run failed: {exc}") from exc


@app.post("/v1/jobs")
def create_job(
    request: JobRequest,
    principal=jobs_write_dependency,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    payload = request.model_dump()
    if idempotency_key:
        try:
            cached = idempotency.get(principal.id, "/v1/jobs", idempotency_key, payload)
        except IdempotencyConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        if cached is not None:
            return cached[1]
    try:
        run_at = datetime.fromisoformat(request.run_at.replace("Z", "+00:00")) if request.run_at else None
    except ValueError as exc:
        raise HTTPException(422, "run_at must be ISO 8601") from exc
    ident = jobs.enqueue(request.goal, run_at)
    response = {"id": ident}
    if idempotency_key:
        idempotency.put(principal.id, "/v1/jobs", idempotency_key, payload, 200, response)
    audit.append(principal.id, "job.create", ident, "success", {"scheduled": bool(run_at)})
    JOBS_CREATED.inc()
    return response


@app.get("/v1/jobs/{job_id}", dependencies=[Depends(auth.dependency("jobs:read"))])
def get_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@app.delete("/v1/jobs/{job_id}", dependencies=[Depends(auth.dependency("jobs:write"))])
def cancel_job(job_id: str):
    status = jobs.request_cancel(job_id)
    if status is None:
        raise HTTPException(404, "job not found")
    if status in {"cancelled", "cancel_requested"}:
        audit.append("api", "job.cancel", job_id, "success", {"status": status})
        return {"id": job_id, "status": status}
    raise HTTPException(409, f"cannot cancel job in {status} state")


@app.get("/v1/jobs/{job_id}/events", dependencies=[Depends(auth.dependency("jobs:read"))])
def get_job_events(job_id: str, after: int = 0):
    if jobs.get(job_id) is None:
        raise HTTPException(404, "job not found")
    return {"events": jobs.events(job_id, after)}


@app.post("/v1/tokens", dependencies=[Depends(auth.dependency("admin"))])
def create_token(request: TokenRequest):
    allowed = {"admin", "runs:write", "jobs:read", "jobs:write"}
    if not request.scopes <= allowed:
        raise HTTPException(422, f"unknown scopes: {sorted(request.scopes - allowed)}")
    ident, token = tokens.create(request.name, request.scopes, request.expires_at)
    audit.append("api", "token.create", ident, "success", {"name": request.name, "scopes": sorted(request.scopes)})
    return {"id": ident, "token": token, "warning": "shown once; store it securely"}


@app.delete("/v1/tokens/{token_id}", dependencies=[Depends(auth.dependency("admin"))])
def revoke_token(token_id: str):
    if not tokens.revoke(token_id):
        raise HTTPException(404, "active token not found")
    audit.append("api", "token.revoke", token_id, "success")
    return {"id": token_id, "revoked": True}


@app.get("/v1/audit", dependencies=[Depends(auth.dependency("admin"))])
def list_audit(after: int = 0, limit: int = 100):
    valid, broken_at = audit.verify()
    if not valid:
        raise HTTPException(500, f"audit chain verification failed at {broken_at}")
    return {"verified": True, "entries": audit.list(after, limit)}


@app.get("/metrics", dependencies=[Depends(auth.dependency("admin"))], include_in_schema=False)
def prometheus_metrics():
    return metrics_response()


@app.get("/", include_in_schema=False)
def web_home(request: Request):
    principal = web_login.authenticate_session(request.cookies.get("meemee_session", "")) if web_login else None
    return WebLogin.home(principal)


@app.get("/auth/login", include_in_schema=False)
def web_login_start():
    if web_login is None:
        raise HTTPException(404, "interactive login is not configured")
    return web_login.start()


@app.get("/auth/callback", include_in_schema=False)
async def web_login_callback(request: Request, code: str, state: str):
    if web_login is None:
        raise HTTPException(404, "interactive login is not configured")
    return await web_login.callback(request, code, state)


@app.post("/auth/logout", include_in_schema=False)
def web_logout():
    response = RedirectResponse("/", 303)
    response.delete_cookie("meemee_session", path="/")
    return response


@app.get("/v1/jobs/{job_id}/stream", dependencies=[Depends(auth.dependency("jobs:read"))])
def stream_job_events(request: Request, job_id: str, after: int = 0):
    if jobs.get(job_id) is None:
        raise HTTPException(404, "job not found")
    last_event = request.headers.get("last-event-id")
    if last_event:
        try:
            after = max(after, int(last_event))
        except ValueError as exc:
            raise HTTPException(400, "Last-Event-ID must be an integer") from exc
    return StreamingResponse(
        job_event_stream(jobs, job_id, after),
        media_type="text/event-stream",
        headers={"Cache-Control":"no-cache, no-transform", "X-Accel-Buffering":"no"},
    )
