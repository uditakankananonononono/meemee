from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .auth import Authenticator, TokenStore
from .config import Settings
from .jobs import JobStore
from .oidc import OIDCConfig, OIDCValidator
from .rate_limit import RateLimitMiddleware
from .runtime import build_agent

settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
log = logging.getLogger("meemee.api")
app = FastAPI(title="Meemee", version=__version__)
app.add_middleware(RateLimitMiddleware, requests=settings.rate_limit_requests, window_seconds=settings.rate_limit_window_seconds)
agent = build_agent(settings)
jobs = JobStore(settings.data_dir / "jobs.sqlite3")
tokens = TokenStore(settings.data_dir / "auth.sqlite3")
oidc = None
if any((settings.oidc_issuer, settings.oidc_audience, settings.oidc_jwks_url)):
    if not all((settings.oidc_issuer, settings.oidc_audience, settings.oidc_jwks_url)):
        raise RuntimeError("OIDC requires issuer, audience and JWKS URL together")
    oidc = OIDCValidator(OIDCConfig(
        settings.oidc_issuer, settings.oidc_audience, settings.oidc_jwks_url,
        settings.oidc_role_claim, settings.oidc_role_scopes,
    ))
auth = Authenticator(tokens, settings.api_token, oidc)


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
        return await agent.run(request.goal, approve=lambda *_: request.approve_writes)
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=502, detail=f"agent run failed: {exc}") from exc


@app.post("/v1/jobs", dependencies=[Depends(auth.dependency("jobs:write"))])
def create_job(request: JobRequest):
    try:
        run_at = datetime.fromisoformat(request.run_at.replace("Z", "+00:00")) if request.run_at else None
    except ValueError as exc:
        raise HTTPException(422, "run_at must be ISO 8601") from exc
    return {"id": jobs.enqueue(request.goal, run_at)}


@app.get("/v1/jobs/{job_id}", dependencies=[Depends(auth.dependency("jobs:read"))])
def get_job(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@app.delete("/v1/jobs/{job_id}", dependencies=[Depends(auth.dependency("jobs:write"))])
def cancel_job(job_id: str):
    if jobs.cancel(job_id):
        return {"id": job_id, "cancelled": True}
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    raise HTTPException(409, f"cannot cancel job in {job['status']} state")


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
    return {"id": ident, "token": token, "warning": "shown once; store it securely"}


@app.delete("/v1/tokens/{token_id}", dependencies=[Depends(auth.dependency("admin"))])
def revoke_token(token_id: str):
    if not tokens.revoke(token_id):
        raise HTTPException(404, "active token not found")
    return {"id": token_id, "revoked": True}
