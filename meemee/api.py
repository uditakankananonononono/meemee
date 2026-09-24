from __future__ import annotations

import asyncio
import hmac
import json
import logging
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
from .account_deletion import AccountPurger, PurgeTargets
from .auth import Authenticator, Principal
from .browser_api import build_browser_router
from .browser_sessions import BrowserSessionManager
from .companion.api import build_companion_router
from .companion.runtime import build_companion
from .config import Settings
from .email_verification import ResendMailer
from .entitlements import public_catalog
from .health import ReadinessChecker
from .idempotency import IdempotencyConflict
from .model_profiles import ModelCatalog, build_role_model, probe_profile, uses_routing
from .monitors import MonitorInput
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
from .persistence import persistence_from_settings
from .personal_model import PersonalItemInput
from .quotas import QuotaExceeded
from .rate_limit import RateLimitMiddleware, SQLiteRateLimiter
from .reflection import PersonalModelReflector
from .runtime import build_agent
from .shutdown import RunGate
from .streaming import job_event_stream
from .web_login import WebLogin, WebLoginConfig
from .webhooks import (
    WebhookStore,
    check_private_hosts_override,
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
    browser_sessions.shutdown()
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
persistence = persistence_from_settings(settings)
browser_sessions = BrowserSessionManager(
    persistence.browser_sessions,  # PostgreSQL mode: shared records; the live browser stays on this host
    headless=settings.browser_headless,
    profiles_dir=settings.data_dir / "browser-profiles",
    public_url=settings.public_url,
    allow_private_hosts=settings.browser_allow_private_hosts,
    max_sessions=settings.browser_max_sessions,
    idle_timeout_seconds=settings.browser_idle_timeout_seconds,
    takeover_ttl_seconds=settings.browser_takeover_ttl_seconds,
    notices=persistence.browser_notices,  # PostgreSQL mode: one queue for every host
)
agent = build_agent(settings, memory=persistence.memory, browser_sessions=browser_sessions, persistence=persistence)
reflection_model = agent.model if not uses_routing(settings) else build_role_model(settings, "reflection")
run_gate = RunGate()
jobs = persistence.jobs
if persistence.backend == "postgresql":
    from meemee_persist_pg.rate_limit import PostgreSQLRateLimiter
    rate_limiter = PostgreSQLRateLimiter(persistence.database, settings.rate_limit_requests, settings.rate_limit_window_seconds)
else:
    rate_limiter = SQLiteRateLimiter(settings.data_dir / "rate-limits.sqlite3", settings.rate_limit_requests, settings.rate_limit_window_seconds)
app.add_middleware(RateLimitMiddleware, limiter=rate_limiter)
runs = persistence.runs  # PostgreSQL mode: run history and idempotency keys shared by every host
idempotency = persistence.idempotency
quotas = persistence.quotas  # PostgreSQL mode: one daily counter per principal across hosts
entitlements = persistence.entitlements
tokens = persistence.tokens  # PostgreSQL mode: shared by every API host
email_verifications = persistence.email_verifications  # PostgreSQL mode: links work on every host
mailer = ResendMailer(settings.resend_api_key, settings.email_from_address, settings.public_url, settings.resend_api_url)
personal_model = persistence.personal_model  # PostgreSQL mode: shared personal model
monitors = persistence.monitors  # PostgreSQL mode: shared by every host
audit = persistence.audit  # PostgreSQL mode: one global chain for every host
approvals = persistence.approvals  # PostgreSQL mode: shared with every worker, not local disk
check_private_hosts_override("api")  # raises when MEEMEE_ENV=production
webhooks = persistence.webhooks or WebhookStore(settings.data_dir / "webhooks.sqlite3", settings.webhook_max_payload_bytes, settings.vault_key)  # PostgreSQL mode: shared outbox
companion = build_companion(settings, store=persistence.companion, persistence=persistence)  # PostgreSQL mode: shared companion tables
deletion_ledger = persistence.deletion_ledger  # PostgreSQL mode: shared, any host resumes a deletion
account_purger = AccountPurger(PurgeTargets(
    jobs=jobs, runs=runs, memory=persistence.memory, idempotency=idempotency, quotas=quotas,
    entitlements=entitlements, approvals=approvals, monitors=monitors, personal_model=personal_model,
    context=persistence.context, webhooks=webhooks, companion=companion.store,
    browser_sessions=browser_sessions.store, browser_notices=browser_sessions.notices,
    reflection_schedule=persistence.reflection_schedule,
), deletion_ledger)
for _resumed in account_purger.resume_incomplete():
    log.warning("resumed interrupted account deletion", extra={"deletion_id": _resumed["deletion_id"]})
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
readiness = ReadinessChecker(settings.data_dir, {"memory": persistence.check_memory, "jobs": persistence.check_jobs, "runs": runs.ping, "tokens": tokens.ping, "entitlements": entitlements.ping, "webhooks": webhooks.ping, "companion": companion.store.ping, "personal_model": personal_model.ping, "context": persistence.context.ping, "monitors": monitors.ping, "reflection_schedule": persistence.reflection_schedule.ping, "account_deletions": deletion_ledger.ping, "browser_sessions": browser_sessions.store.ping, "browser_notices": browser_sessions.notices.ping}, settings.model_base_url, settings.readiness_min_free_bytes, require_model=settings.readiness_require_model)
jobs_write_auth = auth.dependency("jobs:write")
jobs_write_dependency = Depends(jobs_write_auth)
runs_write_dependency = Depends(auth.dependency("runs:write"))
jobs_read_dependency = Depends(auth.dependency("jobs:read"))
app.include_router(build_companion_router(companion.store, companion.engine, companion.channels, auth, audit))
app.include_router(build_browser_router(browser_sessions, auth, audit))
browser_sessions.notice_delivery = lambda: browser_sessions.notices.deliver_pending(browser_sessions.store, companion.store, companion.channels)


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
    response.headers.setdefault("Content-Security-Policy", (
        "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; "
        "form-action 'self'; object-src 'none'; connect-src 'self'"
    ))
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


class MonitorRequest(MonitorInput):
    pass


class PersonalModelRequest(PersonalItemInput):
    pass


class PersonalModelCorrection(BaseModel):
    value: str = Field(min_length=1, max_length=10_000)


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


class TokenIntrospectionRequest(BaseModel):
    token: str = Field(min_length=1, max_length=4096)


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
    active_webhooks = webhooks.active_count(principal.id)
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
    # Disable first so no new request can write under this identity while data is purged.
    if not tokens.disable_account(principal.id):
        raise HTTPException(409, "account is already disabled")
    result = account_purger.purge(principal.id, requested_by=principal.id)
    companion_records = result["steps"].get("companion", {})
    audit.append(principal.id, "account.delete", principal.id, "success", {"deletion_id": result["deletion_id"], "deleted": result["deleted"]})
    return {"account_id": principal.id, "deleted": True, "sessions_revoked": True, "companion_records": companion_records,
            "deletion_id": result["deletion_id"], "product_records": result["deleted"], "audit_retained": True}


@app.delete("/v1/admin/principals/{principal_id}/data", dependencies=[Depends(auth.dependency("admin"))])
def purge_principal_data(principal_id: str, request: Request):
    """Operator erasure for any principal, including external OIDC identities."""
    if tokens.get_account(principal_id) is not None:
        tokens.disable_account(principal_id)
    result = account_purger.purge(principal_id, requested_by=request.state.principal.id)
    audit.append(request.state.principal.id, "account.purge", principal_id, "success", {"deletion_id": result["deletion_id"], "deleted": result["deleted"]})
    return result


@app.get("/v1/admin/account-deletions/{deletion_id}", dependencies=[Depends(auth.dependency("admin"))])
def get_account_deletion(deletion_id: str):
    record = deletion_ledger.get(deletion_id)
    if record is None:
        raise HTTPException(404, "deletion not found")
    return record


@app.post("/v1/account/api-keys", status_code=201)
def create_account_api_key(request: AccountTokenRequest, principal=jobs_read_dependency):
    allowed = {"runs:write", "jobs:read", "jobs:write", "companion:read", "companion:write"}
    if not request.scopes <= allowed:
        raise HTTPException(422, f"unknown or privileged scopes: {sorted(request.scopes - allowed)}")
    ident, token = tokens.create(request.name, request.scopes, request.expires_at, principal.id, "api")
    audit.append(principal.id, "account.api_key.create", ident, "success", {"scopes": sorted(request.scopes)})
    return {"id": ident, "token": token, "warning": "shown once; store it securely"}


@app.get("/v1/monitors")
def list_monitors(status: str | None = None, principal=jobs_read_dependency):
    return {"monitors": monitors.list(principal.id, status)}


@app.post("/v1/monitors", status_code=201)
def create_monitor(request: MonitorRequest, principal=runs_write_dependency):
    result = monitors.create(principal.id, MonitorInput(**request.model_dump()))
    audit.append(principal.id, "monitor.create", result["id"], "success")
    return result


@app.delete("/v1/monitors/{monitor_id}")
def cancel_monitor(monitor_id: str, principal=runs_write_dependency):
    if not monitors.cancel(principal.id, monitor_id):
        raise HTTPException(404, "active monitor not found")
    audit.append(principal.id, "monitor.cancel", monitor_id, "success")
    return {"id": monitor_id, "cancelled": True}


@app.get("/v1/monitors/{monitor_id}/events")
def monitor_events(monitor_id: str, principal=jobs_read_dependency):
    if monitors.get(principal.id, monitor_id) is None:
        raise HTTPException(404, "monitor not found")
    return {"events": monitors.events(principal.id, monitor_id)}


@app.post("/v1/personal-model/reflect")
async def reflect_personal_model(principal=runs_write_dependency):
    context_store = agent.context
    if context_store is None:
        raise HTTPException(503, "unified context is not configured")
    reflector = PersonalModelReflector(context_store, personal_model, reflection_model)
    result = await reflector.reflect(principal.id)
    audit.append(principal.id, "personal_model.reflect", principal.id, "success", {"accepted": result["accepted"], "rejected": result["rejected"]})
    return result


@app.get("/v1/models/status")
async def models_status(probe: bool = False, principal=jobs_read_dependency):
    """Model profiles, routes and (optionally) live reachability of routed profiles.

    Never returns API keys, only whether one is configured. Probing hits each routed,
    usable profile's /models endpoint (no completion, no tokens spent).
    """
    catalog = ModelCatalog.from_settings(settings)
    body = {
        "allow_paid_models": catalog.allow_paid,
        "routes": catalog.routes,
        "profiles": [p.public_dict(catalog.allow_paid) for p in catalog.profiles.values()],
    }
    if probe:
        routed = sorted({name for chain in catalog.routes.values() for name in chain})
        usable = [catalog.profiles[n] for n in routed if catalog.profiles[n].unavailable_reason(catalog.allow_paid) is None]
        results = await asyncio.gather(*(probe_profile(p, timeout=3.0) for p in usable))
        body["probes"] = {r["name"]: r for r in results}
        for chain_role, chain in catalog.routes.items():
            first = next((n for n in chain if body["probes"].get(n, {}).get("reachable")), None)
            body.setdefault("serving", {})[chain_role] = first
    return body


@app.get("/v1/personal-model")
def list_personal_model(kind: str | None = None, include_history: bool = False, principal=jobs_read_dependency):
    allowed = {"goal", "relationship", "project", "preference", "routine", "constraint"}
    if kind is not None and kind not in allowed:
        raise HTTPException(422, "invalid personal-model kind")
    return {"items": personal_model.list(principal.id, kind, include_history)}


@app.post("/v1/personal-model", status_code=201)
def write_personal_model(request: PersonalModelRequest, principal=runs_write_dependency):
    result = personal_model.upsert(principal.id, PersonalItemInput(**request.model_dump()))
    audit.append(principal.id, "personal_model.upsert", result["id"], "success", {"kind": result["kind"]})
    return result


@app.post("/v1/personal-model/{item_id}/correct")
def correct_personal_model(item_id: str, request: PersonalModelCorrection, principal=runs_write_dependency):
    result = personal_model.correct(principal.id, item_id, request.value)
    if result is None:
        raise HTTPException(404, "active personal-model item not found")
    audit.append(principal.id, "personal_model.correct", result["id"], "success", {"supersedes": item_id})
    return result


@app.get("/v1/personal-model/{item_id}/evidence")
def personal_model_evidence(item_id: str, principal=jobs_read_dependency):
    result = personal_model.get(principal.id, item_id)
    if result is None:
        raise HTTPException(404, "personal-model item not found")
    return {"item_id": item_id, "evidence": result["evidence"], "supersedes_id": result["supersedes_id"], "status": result["status"]}


@app.delete("/v1/personal-model/{item_id}")
def delete_personal_model(item_id: str, principal=runs_write_dependency):
    if not personal_model.delete(principal.id, item_id):
        raise HTTPException(404, "personal-model item not found")
    audit.append(principal.id, "personal_model.delete", item_id, "success")
    return {"id": item_id, "deleted": True}


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
    started_at = datetime.now(timezone.utc).isoformat()
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
        if account_purger.discard_late_run(principal.id, report, started_at):
            audit.append("api", "run.discard", report.run_id, "success", {"reason": "account deleted during run"})
            raise HTTPException(410, "account was deleted while this run was in progress; its output was discarded")
        runs.add(principal.id, report)
        audit.append("api", "run.create", report.run_id, "success",
                     {"steps": report.steps_used, "blocked": report.blocked, "refusals": len(report.approvals_required)})
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
        try:  # claim is atomic, so concurrent retries (even on other hosts) cannot both create a job
            cached = idempotency.claim(principal.id, "/v1/jobs", idempotency_key, payload)
        except IdempotencyConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if cached is not None:
            return cached[1]
    try:
        try:
            run_at = datetime.fromisoformat(request.run_at.replace("Z", "+00:00")) if request.run_at else None
        except ValueError as exc:
            raise HTTPException(422, "run_at must be ISO 8601") from exc
        try:
            quota = quotas.consume_job(principal.id)
        except QuotaExceeded as exc:
            raise HTTPException(429, str(exc), headers={"Retry-After":"86400"}) from exc
        ident = jobs.enqueue(request.goal, run_at, principal=principal.id)
    except BaseException:
        if idempotency_key:
            idempotency.release(principal.id, "/v1/jobs", idempotency_key)
        raise
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
    return {"jobs": [with_blocked(item) for item in items], "next_cursor": next_cursor}


def with_blocked(job: dict) -> dict:
    """Add a top-level `blocked` flag (true when the job's run refused any tool call) and keep
    `result` a JSON string on every backend: the PostgreSQL store hands back decoded JSONB, which
    broke the SDK's Job model (result: str) for any finished job in PG mode."""
    job = dict(job)
    result = job.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            result = None
    elif result is not None:
        job["result"] = json.dumps(result)
    job["blocked"] = bool(isinstance(result, dict) and result.get("approvals_required"))
    return job


@app.get("/v1/jobs/{job_id}", dependencies=[Depends(auth.dependency("jobs:read"))])
def get_job(request: Request, job_id: str):
    job = jobs.get_owned(job_id, request.state.principal.id)
    if job is None:
        raise HTTPException(404, "job not found")
    return with_blocked(job)


@app.delete("/v1/jobs/{job_id}", dependencies=[Depends(auth.dependency("jobs:write"))])
def cancel_job(request: Request, job_id: str):
    if jobs.get_owned(job_id, request.state.principal.id) is None:
        raise HTTPException(404, "job not found")
    status = jobs.request_cancel(job_id)
    if status is None:
        raise HTTPException(404, "job not found")
    if status in {"cancelled", "cancel_requested"}:
        if status == "cancelled":
            webhooks.enqueue(f"job:{job_id}:cancelled", "job.cancelled", {"job_id": job_id, "status": "cancelled"}, principal=request.state.principal.id)
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
    try:
        ident, token = tokens.create(request.name, request.scopes, request.expires_at)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit.append("api", "token.create", ident, "success", {"name": request.name, "scopes": sorted(request.scopes)})
    return {"id": ident, "token": token, "warning": "shown once; store it securely"}


@app.get("/v1/tokens", dependencies=[Depends(auth.dependency("admin"))])
def list_tokens(revoked: bool | None = None, before: str | None = None, limit: int = 100, cursor: str | None = None):
    try: items, next_cursor = tokens.list_metadata(revoked, before, limit, cursor)
    except ValueError as exc: raise HTTPException(422, str(exc)) from exc
    return {"tokens": items, "next_cursor": next_cursor}


def redact_token(token: str) -> str:
    """Enough to recognise a credential in a UI, never enough to use it."""
    if len(token) <= 12:
        return "****"
    return f"{token[:4]}...{token[-4:]}"


@app.post("/v1/tokens/introspect", dependencies=[Depends(auth.dependency("admin"))])
def introspect_token(request: TokenIntrospectionRequest):
    """Report a credential's scopes, expiry and revocation state (RFC 7662 style).

    POST keeps the secret out of URLs and access logs. The response never
    contains the secret or its digest; ``redacted_token`` shows only its ends.
    Unknown credentials answer ``active: false, state: unknown`` rather than 404,
    so the endpoint does not distinguish "never issued" from other failures by status.
    Introspection does not count as use: ``last_used_at`` is unchanged.
    """
    supplied = request.token
    base = {"redacted_token": redact_token(supplied)}
    if settings.api_token and hmac.compare_digest(supplied, settings.api_token):
        result = {**base, "active": True, "state": "active", "credential": "bootstrap",
                  "principal": "bootstrap", "name": "bootstrap",
                  "scopes": ["admin", "companion:read", "companion:write", "jobs:read", "jobs:write", "runs:write"],
                  "expires_at": None, "revoked_at": None}
    elif (record := tokens.introspect(supplied)) is not None:
        result = {**base, **record}
    elif oidc is not None and (claims := oidc.authenticate(supplied)) is not None:
        result = {**base, "active": True, "state": "active", "credential": "oidc",
                  "principal": claims.id, "name": claims.name, "scopes": sorted(claims.scopes),
                  "expires_at": None, "revoked_at": None}
    else:
        result = {**base, "active": False, "state": "unknown", "credential": None, "scopes": []}
    audit.append("api-admin", "token.introspect", result.get("id") or result["credential"] or "unknown",
                 "success", {"state": result["state"], "credential": result["credential"]})
    return result


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
    """Stream owner-scoped durable job events over an authenticated WebSocket.

    The handshake is accepted before authorization so a rejection reaches real
    network clients as a 44xx close code; closing before accept makes ASGI
    servers answer a bare HTTP 403 that hides the reason.
    """
    await websocket.accept()
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
    try:
        while True:
            events = jobs.events(job_id, after)
            for event in events:
                after = event["sequence"]
                # Same encoding as the SSE stream: PostgreSQL rows carry UUID and datetime values.
                await websocket.send_text(json.dumps(event, separators=(",", ":"), default=str))
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
    try:
        approvals.grant(principal_id, request.tool, "api-admin", request.expires_at, request.argument_constraints)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
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
    active_webhooks = webhooks.active_count(principal.id)
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
    current = webhooks.active_count(principal.id)
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
    try:
        items, next_cursor = webhooks.list_subscriptions(request.state.principal.id, limit, cursor)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
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
    match = webhooks.get_delivery(request.state.principal.id, delivery_id)
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
    event_id = webhooks.enqueue_test(webhook_id, principal.id)
    if event_id is None:
        raise HTTPException(404, "active webhook not found")
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
