from __future__ import annotations

import asyncio
import hmac
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from console.mount import mount_console
from product_site.mount import mount_site
from webapp.mount import mount_webapp

from . import __version__
from .approvals import ApprovalStore
from .audit import AuditLog
from .auth import Authenticator, Principal, TokenStore
from .companion.api import build_companion_router
from .companion.runtime import build_companion
from .config import Settings
from .email_verification import EmailVerificationStore, ResendMailer
from .entitlements import EntitlementStore, public_catalog
from .health import ReadinessChecker
from .idempotency import IdempotencyConflict, IdempotencyStore
from .observability import (
    AGENT_RUNS,
    JOBS_CREATED,
    WEBHOOK_OLDEST_QUEUED_SECONDS,
    WEBHOOK_OUTBOX,
    WEBHOOK_SUCCESS_RATE,
    WEBHOOK_SUSPENDED,
    MetricsMiddleware,
    configure_logging,
    metrics_response,
)
from .oidc import OIDCConfig, OIDCValidator
from .persistence import build_persistence
from .quotas import QuotaExceeded, QuotaStore
from .rate_limit import RateLimitMiddleware, SQLiteRateLimiter
from .runs import RunStore
from .runtime import build_agent
from .shutdown import RunGate
from .streaming import job_event_stream
from .web_login import WebLogin, WebLoginConfig
from .webhooks import (
    WebhookStore,
    delivery_metrics,
    list_deliveries,
    operational_metrics,
    replay_delivery,
)

settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
if (settings.data_dir / ".key-rotation-in-progress").exists():
    raise RuntimeError("incomplete encryption-key rotation; restore the pre-rotation backup")
configure_logging(settings.log_level, settings.log_json)
log = logging.getLogger("meemee.api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield
    drained = await run_gate.drain(settings.shutdown_grace_seconds)
    if not drained:
        log.warning("shutdown grace period elapsed", extra={"active_runs": run_gate.active})
    close = getattr(agent.model, "aclose", None)
    if close is not None:
        await close()
    persistence.close()

app = FastAPI(title="Meemee", version=__version__, lifespan=lifespan)
trusted_hosts = [host.strip() for host in settings.trusted_hosts.split(",") if host.strip()]
if not trusted_hosts:
    raise RuntimeError("MEEMEE_TRUSTED_HOSTS must contain at least one host")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)
mount_console(app)
mount_webapp(app)
mount_site(app)
app.add_middleware(MetricsMiddleware)
persistence = build_persistence(settings.persistence_backend, settings.data_dir, settings.postgres_dsn)
agent = build_agent(settings, memory=persistence.memory)
run_gate = RunGate()
jobs = persistence.jobs
if persistence.backend == "postgresql":
    from meemee_persist_pg.rate_limit import PostgreSQLRateLimiter
    rate_limiter = PostgreSQLRateLimiter(persistence.database, settings.rate_limit_requests, settings.rate_limit_window_seconds)
else:
    rate_limiter = SQLiteRateLimiter(settings.data_dir / "rate-limits.sqlite3", settings.rate_limit_requests, settings.rate_limit_window_seconds)
app.add_middleware(RateLimitMiddleware, limiter=rate_limiter)
runs = RunStore(settings.data_dir / "runs.sqlite3")
idempotency = IdempotencyStore(settings.data_dir / "idempotency.sqlite3")
quotas = QuotaStore(settings.data_dir / "quotas.sqlite3", settings.default_daily_jobs)
entitlements = EntitlementStore(settings.data_dir / "entitlements.sqlite3", settings.default_plan)
tokens = TokenStore(settings.data_dir / "auth.sqlite3")
email_verifications = EmailVerificationStore(settings.data_dir / "email-verifications.sqlite3")
mailer = ResendMailer(settings.resend_api_key, settings.email_from_address, settings.public_url)
audit = AuditLog(settings.data_dir / "audit.sqlite3")
approvals = ApprovalStore(settings.data_dir / "approvals.sqlite3")
webhooks = WebhookStore(settings.data_dir / "webhooks.sqlite3", settings.webhook_max_payload_bytes, settings.vault_key)
companion = build_companion(settings)
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
readiness = ReadinessChecker(settings.data_dir, {"memory": persistence.check_memory, "jobs": persistence.check_jobs, "runs": lambda: runs.db.execute("SELECT 1").fetchone(), "tokens": lambda: tokens.db.execute("SELECT 1").fetchone(), "entitlements": lambda: entitlements.db.execute("SELECT 1").fetchone()}, settings.model_base_url, settings.readiness_min_free_bytes, require_model=settings.readiness_require_model)
jobs_write_auth = auth.dependency("jobs:write")
jobs_write_dependency = Depends(jobs_write_auth)
runs_write_dependency = Depends(auth.dependency("runs:write"))
jobs_read_dependency = Depends(auth.dependency("jobs:read"))
app.include_router(build_companion_router(companion.store, companion.engine, companion.channels, auth, audit))


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
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'; object-src 'none'; connect-src 'self'"
    )
    if request.url.path.startswith(("/v1/", "/auth/")):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
    if settings.hsts_enabled:
        response.headers["Strict-Transport-Security"] = (
            f"max-age={settings.hsts_max_age_seconds}; includeSubDomains"
        )
    return response


class RunRequest(BaseModel):
    goal: str = Field(min_length=2, max_length=20_000)
    approve_writes: bool = False
    approved_tools: set[str] = Field(default_factory=set, max_length=100)


class EmailTaskRequest(BaseModel):
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20_000)


class JobRequest(BaseModel):
    goal: str = Field(min_length=2, max_length=20_000)
    run_at: str | None = None


class SignupRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=200)
    display_name: str = Field(min_length=1, max_length=120)


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


class AccountTokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: set[str] = Field(min_length=1)
    expires_at: str | None = None


class TokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: set[str] = Field(min_length=1)
    expires_at: str | None = None


@app.get("/health")
def health():
    return {"status": "ok", "version": __version__}


@app.get("/ready")
async def ready():
    result = await readiness.check()
    if result["status"] != "ready":
        return JSONResponse(result, status_code=503)
    return result


@app.get("/v1/whoami")
def whoami(principal=jobs_read_dependency):
    entitlement = entitlements.get(principal.id)
    active_webhooks = webhooks.db.execute(
        "SELECT count(*) FROM webhook_subscriptions WHERE principal=? AND active=1", (principal.id,)
    ).fetchone()[0]
    quota = quotas.status(principal.id)
    entitlement["usage"] = {
        "daily_jobs": quota["used"],
        "webhooks": int(active_webhooks),
        "persistent_approvals": approvals.active_count(principal.id),
    }
    return {
        "id": principal.id,
        "name": principal.name,
        "scopes": sorted(principal.scopes),
        "entitlement": entitlement,
    }


@app.post("/v1/accounts/signup", status_code=201)
def account_signup(request: SignupRequest):
    if not settings.signup_enabled:
        raise HTTPException(404, "self-serve signup is disabled")
    try:
        account, token = tokens.create_account(request.email, request.password, request.display_name)
    except ValueError as exc:
        raise HTTPException(409 if "already exists" in str(exc) else 422, str(exc)) from exc
    entitlements.get(account["id"])
    audit.append(account["id"], "account.signup", account["id"], "success")
    raw = email_verifications.issue(account["id"])
    email_delivery = "not_configured"
    if mailer.configured:
        mailer.send_verification(account["email"], account["id"], raw)
        email_delivery = "sent"
    return {"account": {**account, "email_verified": False}, "token": token, "token_type": "bearer", "expires_in_days": 30, "verification_email": email_delivery}


class EmailVerifyRequest(BaseModel):
    account_id: str = Field(min_length=8, max_length=80)
    token: str = Field(min_length=20, max_length=200)


@app.post("/v1/accounts/verify-email")
def verify_account_email(request: EmailVerifyRequest):
    if not email_verifications.verify(request.account_id, request.token):
        raise HTTPException(400, "invalid or expired verification link")
    audit.append(request.account_id, "account.email.verify", request.account_id, "success")
    return {"account_id": request.account_id, "email_verified": True}


class PasswordForgotRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class PasswordResetRequest(BaseModel):
    account_id: str = Field(min_length=8, max_length=80)
    token: str = Field(min_length=20, max_length=200)
    password: str = Field(min_length=12, max_length=200)


@app.post("/v1/accounts/forgot-password", status_code=202)
def forgot_password(request: PasswordForgotRequest):
    account = tokens.account_by_email(request.email)
    if account is not None and mailer.configured:
        raw = email_verifications.issue_password_reset(account["id"])
        mailer.send_password_reset(account["email"], account["id"], raw)
    return {"status": "accepted", "detail": "If that account exists, a reset email was sent."}


@app.post("/v1/accounts/reset-password")
def reset_password(request: PasswordResetRequest):
    if not email_verifications.consume_password_reset(request.account_id, request.token):
        raise HTTPException(400, "invalid or expired reset link")
    try:
        changed = tokens.reset_password(request.account_id, request.password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if not changed:
        raise HTTPException(400, "invalid or expired reset link")
    audit.append(request.account_id, "account.password.reset", request.account_id, "success")
    return {"account_id": request.account_id, "password_reset": True, "sessions_revoked": True}


@app.post("/v1/accounts/login")
def account_login(request: LoginRequest):
    result = tokens.login_account(request.email, request.password)
    if result is None:
        raise HTTPException(401, "invalid email or password")
    account, token = result
    audit.append(account["id"], "account.login", account["id"], "success")
    return {"account": account, "token": token, "token_type": "bearer", "expires_in_days": 30}


@app.get("/v1/account")
def get_account(principal=jobs_read_dependency):
    account = tokens.get_account(principal.id)
    if account is None:
        return {"id": principal.id, "display_name": principal.name, "email": None, "external": True}
    return {**account, "external": False, "email_verified": email_verifications.status(principal.id)}


@app.get("/v1/account/export")
def export_my_account(principal=jobs_read_dependency):
    account = tokens.get_account(principal.id)
    if account is None:
        raise HTTPException(404, "built-in account not found")
    return {"format": "meemee.customer-export.v1", "exported_at": datetime.now(timezone.utc).isoformat(), "account": account, "companion": companion.store.export_user_data(principal.id)}


@app.delete("/v1/account")
def delete_my_account(principal=jobs_read_dependency):
    if tokens.get_account(principal.id) is None:
        raise HTTPException(404, "built-in account not found")
    deleted = companion.store.delete_user_data(principal.id)
    if not tokens.disable_account(principal.id):
        raise HTTPException(409, "account is already disabled")
    audit.append(principal.id, "account.delete", principal.id, "success", deleted)
    return {"account_id": principal.id, "deleted": True, "sessions_revoked": True, "companion_records": deleted, "audit_retained": True}


@app.post("/v1/account/api-keys", status_code=201)
def create_account_api_key(request: AccountTokenRequest, principal=jobs_read_dependency):
    allowed = {"runs:write", "jobs:read", "jobs:write", "companion:read", "companion:write"}
    if not request.scopes <= allowed:
        raise HTTPException(422, f"unknown or privileged scopes: {sorted(request.scopes - allowed)}")
    ident, token = tokens.create(request.name, request.scopes, request.expires_at, principal.id, "api")
    audit.append(principal.id, "account.api_key.create", ident, "success", {"scopes": sorted(request.scopes)})
    return {"id": ident, "token": token, "warning": "shown once; store it securely"}


@app.get("/v1/email-bridge/status")
def email_bridge_status(principal=jobs_read_dependency):
    account = tokens.get_account(principal.id)
    return {
        "outbound_configured": mailer.configured,
        "recipient": account["email"] if account else None,
        "gmail_connected": bool(settings.gmail_access_token),
        "gmail_connection_required": not bool(settings.gmail_access_token),
        "gmail_required_scope": "https://www.googleapis.com/auth/gmail.readonly",
    }


@app.post("/v1/email-bridge/send-task")
def send_email_task(request: EmailTaskRequest, principal=runs_write_dependency):
    account = tokens.get_account(principal.id)
    if account is None or not account.get("email"):
        raise HTTPException(409, "the authenticated account has no email recipient")
    if not email_verifications.status(principal.id):
        raise HTTPException(403, "verify the recipient email before sending tasks")
    try:
        delivery_id = mailer.send_task(account["email"], request.subject, request.body, settings.email_reply_to)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    audit.append(principal.id, "email_bridge.task.send", delivery_id, "success", {"recipient": account["email"]})
    return {"delivery_id": delivery_id, "recipient": account["email"], "reply_to": settings.email_reply_to}


@app.get("/v1/account/api-keys")
def list_account_api_keys(revoked: bool | None = False, limit: int = 100, cursor: str | None = None, principal=jobs_read_dependency):
    try:
        items, next_cursor = tokens.list_metadata(revoked=revoked, limit=limit, cursor=cursor, owner_id=principal.id, token_kind="api")
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"api_keys": items, "next_cursor": next_cursor}


@app.delete("/v1/account/api-keys/{token_id}")
def revoke_account_api_key(token_id: str, principal=jobs_read_dependency):
    if not tokens.revoke(token_id, owner_id=principal.id):
        raise HTTPException(404, "active API key not found")
    audit.append(principal.id, "account.api_key.revoke", token_id, "success")
    return {"id": token_id, "revoked": True}


@app.post("/v1/runs")
async def create_run(request: RunRequest, principal=runs_write_dependency):
    if not run_gate.accepting:
        raise HTTPException(503, "server is draining", headers={"Retry-After":"30"})
    await run_gate.enter()
    try:
        report = await agent.run(
            request.goal,
            approve=lambda name, _arguments, _risk: (
                request.approve_writes
                or name in request.approved_tools
                or approvals.allows(principal.id, name, arguments=_arguments)
            ),
            owner_id=principal.id,
        )
        runs.add(principal.id, report)
        audit.append("api", "run.create", report.run_id, "success", {"steps": report.steps_used})
        AGENT_RUNS.labels("success").inc()
        return report
    except (OSError, ValueError, RuntimeError) as exc:
        AGENT_RUNS.labels("failed").inc()
        raise HTTPException(status_code=502, detail=f"agent run failed: {exc}") from exc
    finally:
        await run_gate.leave()


@app.get("/v1/runs", dependencies=[Depends(auth.dependency("runs:write"))])
def list_runs(request: Request, before: str | None = None, limit: int = 100, cursor: str | None = None):
    try: items, next_cursor = runs.list(request.state.principal.id, before, limit, cursor)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    return {"runs": items, "next_cursor": next_cursor}


@app.get("/v1/runs/{run_id}", dependencies=[Depends(auth.dependency("runs:write"))])
def get_run(request: Request, run_id: str):
    result = runs.get(request.state.principal.id, run_id)
    if result is None:
        raise HTTPException(404, "run not found")
    return result


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
    try:
        quota = quotas.consume_job(principal.id)
    except QuotaExceeded as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After":"86400"}) from exc
    ident = jobs.enqueue(request.goal, run_at, principal=principal.id)
    response = {"id": ident, "quota": quota}
    if idempotency_key:
        idempotency.put(principal.id, "/v1/jobs", idempotency_key, payload, 200, response)
    audit.append(principal.id, "job.create", ident, "success", {"scheduled": bool(run_at)})
    JOBS_CREATED.inc()
    return response


@app.get("/v1/jobs", dependencies=[Depends(auth.dependency("jobs:read"))])
def list_jobs(request: Request, status: str | None = None, before: str | None = None, limit: int = 100, cursor: str | None = None):
    if status is not None and status not in {"queued","running","done","failed","cancel_requested","cancelled"}:
        raise HTTPException(422, "invalid job status")
    try: items, next_cursor = jobs.list_for_principal(request.state.principal.id, status, before, limit, cursor)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    return {"jobs": items, "next_cursor": next_cursor}


@app.get("/v1/jobs/{job_id}", dependencies=[Depends(auth.dependency("jobs:read"))])
def get_job(request: Request, job_id: str):
    job = jobs.get_owned(job_id, request.state.principal.id)
    if job is None:
        raise HTTPException(404, "job not found")
    return job


@app.delete("/v1/jobs/{job_id}", dependencies=[Depends(auth.dependency("jobs:write"))])
def cancel_job(request: Request, job_id: str):
    if jobs.get_owned(job_id, request.state.principal.id) is None:
        raise HTTPException(404, "job not found")
    status = jobs.request_cancel(job_id)
    if status is None:
        raise HTTPException(404, "job not found")
    if status in {"cancelled", "cancel_requested"}:
        if status == "cancelled":
            webhooks.enqueue(f"job:{job_id}:cancelled", "job.cancelled", {"job_id": job_id, "status": "cancelled"})
        audit.append("api", "job.cancel", job_id, "success", {"status": status})
        return {"id": job_id, "status": status}
    raise HTTPException(409, f"cannot cancel job in {status} state")


@app.get("/v1/jobs/{job_id}/events", dependencies=[Depends(auth.dependency("jobs:read"))])
def get_job_events(request: Request, job_id: str, after: int = 0):
    if jobs.get_owned(job_id, request.state.principal.id) is None:
        raise HTTPException(404, "job not found")
    return {"events": jobs.events(job_id, after)}


@app.post("/v1/tokens", dependencies=[Depends(auth.dependency("admin"))])
def create_token(request: TokenRequest):
    allowed = {"admin", "runs:write", "jobs:read", "jobs:write", "companion:read", "companion:write"}
    if not request.scopes <= allowed:
        raise HTTPException(422, f"unknown scopes: {sorted(request.scopes - allowed)}")
    ident, token = tokens.create(request.name, request.scopes, request.expires_at)
    audit.append("api", "token.create", ident, "success", {"name": request.name, "scopes": sorted(request.scopes)})
    return {"id": ident, "token": token, "warning": "shown once; store it securely"}


@app.get("/v1/tokens", dependencies=[Depends(auth.dependency("admin"))])
def list_tokens(revoked: bool | None = None, before: str | None = None, limit: int = 100, cursor: str | None = None):
    try: items, next_cursor = tokens.list_metadata(revoked, before, limit, cursor)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    return {"tokens": items, "next_cursor": next_cursor}


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
    entries, next_cursor = audit.list_page(after, limit)
    return {"verified": True, "entries": entries, "next_cursor": next_cursor}


@app.get("/metrics", dependencies=[Depends(auth.dependency("admin"))], include_in_schema=False)
def prometheus_metrics():
    for status, count in delivery_metrics(webhooks).items():
        WEBHOOK_OUTBOX.labels(status).set(count)
    operations = operational_metrics(webhooks)
    WEBHOOK_SUCCESS_RATE.set(operations["success_rate"])
    WEBHOOK_OLDEST_QUEUED_SECONDS.set(operations["oldest_queued_seconds"])
    WEBHOOK_SUSPENDED.set(operations["suspended"])
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
    if jobs.get_owned(job_id, request.state.principal.id) is None:
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


@app.websocket("/v1/jobs/{job_id}/ws")
async def websocket_job_events(websocket: WebSocket, job_id: str):
    """Stream owner-scoped durable job events over an authenticated WebSocket."""
    authorization = websocket.headers.get("authorization", "")
    principal = None
    if authorization.lower().startswith("bearer "):
        supplied = authorization[7:]
        if settings.api_token and hmac.compare_digest(supplied, settings.api_token):
            principal = Principal("bootstrap", "bootstrap", frozenset({"admin", "jobs:read"}))
        else:
            principal = tokens.authenticate(supplied)
            if principal is None and oidc is not None:
                principal = oidc.authenticate(supplied)
    if principal is None and web_login is not None:
        principal = web_login.authenticate_session(websocket.cookies.get("meemee_session", ""))
    if principal is None:
        await websocket.close(code=4401, reason="authentication required"); return
    if "jobs:read" not in principal.scopes and "admin" not in principal.scopes:
        await websocket.close(code=4403, reason="missing jobs:read scope"); return
    if jobs.get_owned(job_id, principal.id) is None:
        await websocket.close(code=4404, reason="job not found"); return
    try: after = int(websocket.query_params.get("after", "0"))
    except ValueError:
        await websocket.close(code=4400, reason="after must be an integer"); return
    await websocket.accept()
    try:
        while True:
            events = jobs.events(job_id, after)
            for event in events:
                after = event["sequence"]
                await websocket.send_json(event)
            current = jobs.get_owned(job_id, principal.id)
            if current and current["status"] in {"done", "failed", "cancelled"} and not events:
                await websocket.close(code=1000); return
            await asyncio.sleep(0.25)
    except WebSocketDisconnect:
        return


@app.get("/v1/quota")
def quota_status(principal=jobs_write_dependency):
    return quotas.status(principal.id)


class QuotaRequest(BaseModel):
    daily_jobs: int = Field(ge=1, le=1_000_000)


@app.put("/v1/quota/{principal_id}", dependencies=[Depends(auth.dependency("admin"))])
def set_quota(principal_id: str, request: QuotaRequest):
    quotas.set_limit(principal_id, request.daily_jobs)
    audit.append("api", "quota.update", principal_id, "success", {"daily_jobs": request.daily_jobs})
    return quotas.status(principal_id)



class ToolApprovalRequest(BaseModel):
    tool: str = Field(min_length=1, max_length=200)
    expires_at: str | None = None
    argument_constraints: dict | None = None


@app.put("/v1/approvals/{principal_id}", dependencies=[Depends(auth.dependency("admin"))])
def grant_tool_approval(principal_id: str, request: ToolApprovalRequest):
    if request.tool not in {schema["name"] for schema in agent.tools.schemas()}:
        raise HTTPException(422, "unknown tool")
    already_active = approvals.allows(principal_id, request.tool)
    if not already_active and not entitlements.allows(
        principal_id, "persistent_approvals", approvals.active_count(principal_id)
    ):
        raise HTTPException(403, "plan persistent approval limit reached")
    approvals.grant(principal_id, request.tool, "api-admin", request.expires_at, request.argument_constraints)
    audit.append("api-admin", "approval.grant", principal_id, "success", {"tool": request.tool, "expires_at": request.expires_at, "argument_constraints": request.argument_constraints})
    return {"principal": principal_id, "tool": request.tool, "granted": True}


@app.delete("/v1/approvals/{principal_id}/{tool_name}", dependencies=[Depends(auth.dependency("admin"))])
def revoke_tool_approval(principal_id: str, tool_name: str):
    if not approvals.revoke(principal_id, tool_name):
        raise HTTPException(404, "active approval not found")
    audit.append("api-admin", "approval.revoke", principal_id, "success", {"tool": tool_name})
    return {"principal": principal_id, "tool": tool_name, "revoked": True}


@app.get("/v1/approvals/{principal_id}", dependencies=[Depends(auth.dependency("admin"))])
def list_tool_approvals(principal_id: str):
    return {"approvals": approvals.list(principal_id)}


class PlanAssignmentRequest(BaseModel):
    plan: str


@app.get("/v1/product/plans")
def product_plans():
    return public_catalog()


@app.get("/v1/entitlements")
def current_entitlements(principal=jobs_write_dependency):
    result = entitlements.get(principal.id)
    active_webhooks = webhooks.db.execute(
        "SELECT count(*) FROM webhook_subscriptions WHERE principal=? AND active=1", (principal.id,)
    ).fetchone()[0]
    quota = quotas.status(principal.id)
    result["usage"] = {
        "daily_jobs": quota["used"],
        "webhooks": int(active_webhooks),
        "persistent_approvals": approvals.active_count(principal.id),
    }
    return result


@app.put("/v1/entitlements/{principal_id}", dependencies=[Depends(auth.dependency("admin"))])
def assign_entitlements(principal_id: str, request: PlanAssignmentRequest):
    try:
        result = entitlements.assign(principal_id, request.plan, datetime.now(timezone.utc).isoformat())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    quotas.set_limit(principal_id, int(result["limits"]["daily_jobs"]))
    audit.append("api-admin", "entitlements.assign", principal_id, "success", {"plan": request.plan})
    return result


class WebhookRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    events: set[str] = Field(min_length=1, max_length=20)
    fields: set[str] = Field(default_factory=set, max_length=50)
    headers: dict[str, str] = Field(default_factory=dict, max_length=20)


@app.post("/v1/webhooks")
def create_webhook(request: WebhookRequest, principal=jobs_write_dependency):
    current = webhooks.db.execute(
        "SELECT count(*) FROM webhook_subscriptions WHERE principal=? AND active=1", (principal.id,)
    ).fetchone()[0]
    if not entitlements.allows(principal.id, "webhooks", current):
        raise HTTPException(403, "plan webhook limit reached")
    allowed = {"job.done", "job.failed", "job.cancelled", "*"}
    if not request.events <= allowed:
        raise HTTPException(422, f"unknown webhook events: {sorted(request.events - allowed)}")
    try:
        ident, secret = webhooks.subscribe(principal.id, request.url, request.events, request.fields, request.headers)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit.append(principal.id, "webhook.create", ident, "success", {"url": request.url, "events": sorted(request.events), "fields": sorted(request.fields), "header_names": sorted(request.headers)})
    return {"id": ident, "secret": secret, "warning": "shown once; store it securely"}


@app.get("/v1/webhooks", dependencies=[Depends(auth.dependency("jobs:read"))])
def list_webhooks(request: Request, cursor: str | None = None, limit: int = 100):
    from .cursors import decode_cursor, encode_cursor

    principal = request.state.principal
    clauses, parameters = ["principal=?"], [principal.id]
    if cursor:
        try: cursor_time, cursor_id = decode_cursor(cursor)
        except ValueError as exc: raise HTTPException(422, str(exc)) from exc
        clauses.append("(created_at<? OR (created_at=? AND id<?))")
        parameters.extend((cursor_time, cursor_time, cursor_id))
    page_size = min(max(limit, 1), 500); parameters.append(page_size + 1)
    rows = webhooks.db.execute(
        f"SELECT id,url,events,active,created_at FROM webhook_subscriptions WHERE {' AND '.join(clauses)} ORDER BY created_at DESC,id DESC LIMIT ?",
        tuple(parameters),
    ).fetchall()
    items = [dict(row) for row in rows[:page_size]]
    next_cursor = encode_cursor(items[-1]["created_at"], items[-1]["id"]) if len(rows)>page_size else None
    return {"webhooks": items, "next_cursor": next_cursor}


@app.delete("/v1/webhooks/{webhook_id}")
def delete_webhook(webhook_id: str, principal=jobs_write_dependency):
    if not webhooks.unsubscribe(webhook_id, principal.id):
        raise HTTPException(404, "active webhook not found")
    audit.append(principal.id, "webhook.delete", webhook_id, "success")
    return {"id": webhook_id, "deleted": True}


@app.get("/v1/webhook-deliveries", dependencies=[Depends(auth.dependency("jobs:read"))])
def get_webhook_deliveries(request: Request, status: str | None = None, after: str | None = None, limit: int = 100, cursor: str | None = None):
    if status and status not in {"queued", "sending", "delivered", "failed"}:
        raise HTTPException(422, "invalid delivery status")
    try: items, next_cursor = list_deliveries(webhooks, request.state.principal.id, status, after, limit, cursor)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    return {"deliveries": items, "next_cursor": next_cursor}


@app.get("/v1/webhook-deliveries/{delivery_id}", dependencies=[Depends(auth.dependency("jobs:read"))])
def get_webhook_delivery(request: Request, delivery_id: str):
    rows = list_deliveries(webhooks, request.state.principal.id, after=None, limit=500)[0]
    match = next((row for row in rows if row["id"] == delivery_id), None)
    if match is None:
        raise HTTPException(404, "delivery not found")
    return match


@app.post("/v1/webhook-deliveries/{delivery_id}/replay", dependencies=[Depends(auth.dependency("jobs:write"))])
def replay_webhook_delivery(request: Request, delivery_id: str):
    principal = request.state.principal
    if not replay_delivery(webhooks, principal.id, delivery_id):
        raise HTTPException(409, "delivery is not a replayable failed delivery")
    audit.append(principal.id, "webhook.delivery.replay", delivery_id, "success")
    return {"id": delivery_id, "status": "queued"}


@app.post("/v1/webhooks/{webhook_id}/rotate-secret")
def rotate_webhook_secret(webhook_id: str, principal=jobs_write_dependency):
    secret = webhooks.rotate_secret(webhook_id, principal.id)
    if secret is None:
        raise HTTPException(404, "active webhook not found")
    audit.append(principal.id, "webhook.secret.rotate", webhook_id, "success")
    return {"id": webhook_id, "secret": secret, "warning": "shown once; store it securely"}


@app.post("/v1/webhooks/{webhook_id}/test")
def test_webhook(webhook_id: str, principal=jobs_write_dependency):
    owned = webhooks.db.execute("SELECT 1 FROM webhook_subscriptions WHERE id=? AND principal=? AND active=1", (webhook_id, principal.id)).fetchone()
    if owned is None:
        raise HTTPException(404, "active webhook not found")
    event_id = f"test:{uuid.uuid4().hex}"
    payload = json.dumps({"webhook_id": webhook_id, "test": True}, sort_keys=True, separators=(",", ":"))
    with webhooks.lock, webhooks.db:
        webhooks.db.execute("INSERT INTO webhook_deliveries(id,subscription_id,event_id,event_type,payload,status,next_attempt_at,created_at) VALUES(?,?,?,?,?,'queued',?,?)", (uuid.uuid4().hex, webhook_id, event_id, "webhook.test", payload, time.time(), datetime.now(timezone.utc).isoformat()))
    audit.append(principal.id, "webhook.test", webhook_id, "success", {"event_id": event_id})
    return {"event_id": event_id, "queued": True}


@app.post("/v1/webhooks/{webhook_id}/pause")
def pause_webhook(webhook_id: str, principal=jobs_write_dependency):
    if not webhooks.set_active(webhook_id, principal.id, False):
        raise HTTPException(404, "webhook not found")
    audit.append(principal.id, "webhook.pause", webhook_id, "success")
    return {"id": webhook_id, "active": False}


@app.post("/v1/webhooks/{webhook_id}/resume")
def resume_webhook(webhook_id: str, principal=jobs_write_dependency):
    try:
        resumed = webhooks.set_active(
            webhook_id,
            principal.id,
            True,
            settings.webhook_breaker_cooldown_seconds,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if not resumed:
        raise HTTPException(404, "webhook not found")
    audit.append(principal.id, "webhook.resume", webhook_id, "success")
    return {"id": webhook_id, "active": True}


@app.get("/v1/webhooks/{webhook_id}/health", dependencies=[Depends(auth.dependency("jobs:read"))])
def webhook_health(request: Request, webhook_id: str):
    result = webhooks.health(webhook_id, request.state.principal.id)
    if result is None:
        raise HTTPException(404, "webhook not found")
    return result


@app.get("/v1/webhook-deliveries/{delivery_id}/attempts", dependencies=[Depends(auth.dependency("jobs:read"))])
def webhook_delivery_attempts(request: Request, delivery_id: str):
    attempts = webhooks.attempt_timeline(delivery_id, request.state.principal.id)
    if not attempts:
        owned = list_deliveries(webhooks, request.state.principal.id, limit=500)[0]
        if not any(row["id"] == delivery_id for row in owned):
            raise HTTPException(404, "delivery not found")
    return {"attempts": attempts}
