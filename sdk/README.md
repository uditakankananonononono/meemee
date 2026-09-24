# meemee-client

Typed Python SDK for the [Meemee](../README.md) agent platform API. Targets the
server **v0.122.0** HTTP contract: scoped API tokens and OIDC bearer auth,
synchronous runs, durable queued jobs, resume-safe SSE and WebSocket progress,
fixed-window rate limiting, and the tamper-evident audit chain. Every call is
available synchronously (`MeemeeClient`) and on asyncio (`AsyncMeemeeClient`).

Python 3.10+. Dependencies: `httpx`, `pydantic` v2. WebSocket streaming needs
the optional `ws` extra (`websockets`); nothing else.

## Install

```bash
pip install -e ./sdk          # from the repository root
pip install -e './sdk[ws]'    # adds WebSocket job streaming
pip install -e './sdk[dev]'   # with pytest, pytest-asyncio and websockets for the test-suite
pytest sdk/tests              # full mocked and live-server suite
```

## Quick start

```python
from meemee_client import MeemeeClient

with MeemeeClient("http://127.0.0.1:8787", auth="mee_...") as client:
    print(client.health())                       # server version and liveness

    job = client.jobs.create("Summarise today's arXiv cs.AI highlights")
    for event in client.jobs.stream_events(job.id):
        print(event.kind, event.payload)

    finished = client.jobs.get(job.id)
    print(finished.status, finished.result_data)
```

### asyncio

```python
import asyncio
from meemee_client import AsyncMeemeeClient

async def main() -> None:
    async with AsyncMeemeeClient("http://127.0.0.1:8787", auth="mee_...") as client:
        job = await client.jobs.create("Summarise today's arXiv cs.AI highlights")
        async for event in client.jobs.stream_ws(job.id):     # or stream_events (SSE)
            print(event.kind, event.payload)
        async for past in client.jobs.iter_all(status="done"):
            print(past.id)

asyncio.run(main())
```

`AsyncMeemeeClient` has the same resources, arguments, models, errors and retry
policy as `MeemeeClient`; methods are coroutines and iterators are async
iterators. `TokenAuth` is used directly; a provider with an
`async authorization_header_async()` method, such as
`AsyncOIDCClientCredentialsAuth`, is awaited; any other provider (such as the
sync `OIDCClientCredentialsAuth`, whose refresh does blocking HTTP) runs in a
worker thread so a token refresh never blocks the event loop.

```python
from meemee_client import AsyncMeemeeClient, AsyncOIDCClientCredentialsAuth

auth = AsyncOIDCClientCredentialsAuth(
    "https://idp.example.com/realms/meemee",
    client_id="meemee-worker",
    client_secret=os.environ["OIDC_CLIENT_SECRET"],
    scope="operator",
)
async with AsyncMeemeeClient(base_url, auth=auth) as client:
    await client.jobs.list()      # token fetched once, shared by concurrent calls
await auth.aclose()
```

### Expired credentials

When the auth provider has a `refresh()` method (both OIDC providers do), a
401 makes the client call it once and retry the request once. SSE and
WebSocket streams reopen from scratch with the new credential. A second 401
raises `AuthenticationError`, so there is never a loop. Static `TokenAuth`
tokens have no refresh and fail on the first 401, as before.

### WebSocket streaming

`client.jobs.stream_ws(job_id, after=0)` (sync and async) follows
`/v1/jobs/{id}/ws`. It sends the bearer token in the handshake, yields each
JobEvent once in order, and returns after a terminal event or the server's
1000 close. A dropped connection reconnects with `?after=<last sequence>` under
the same budget and backoff as SSE (`max_reconnects`, default 6). The server
sends no idle frames, so dead peers are caught by WebSocket ping/pong
(`ping_interval`/`ping_timeout`, 20s each). Rejections raise typed errors
without reconnecting: 4401 `AuthenticationError`, 4403 `PermissionDeniedError`
(`missing_scope="jobs:read"`), 4404 `NotFoundError`, 4400 `BadRequestError`.
A proxy or older server that refuses the handshake with a plain HTTP status is
mapped the same way; an HTTP 5xx handshake is retried.

More in [../examples](../examples/README.md): quickstart, job watching, manual
SSE resume across process restarts, OIDC machine auth, admin token + audit work.

## Authentication

Two credential families, matching the server:

```python
from meemee_client import MeemeeClient, OIDCClientCredentialsAuth, TokenAuth

# 1. Scoped Meemee API token (mee_...) or the bootstrap token.
client = MeemeeClient(base_url, auth=TokenAuth("mee_..."))
client = MeemeeClient(base_url, auth="mee_...")            # shorthand

# 2. OIDC client-credentials: the SDK discovers the token endpoint from the
#    issuer, fetches access tokens, and refreshes them before expiry. The
#    server validates them against the issuer JWKS and maps roles to scopes.
auth = OIDCClientCredentialsAuth(
    "https://idp.example.com/realms/meemee",
    client_id="meemee-worker",
    client_secret=os.environ["OIDC_CLIENT_SECRET"],
    scope="operator",
)
client = MeemeeClient(base_url, auth=auth)
```

Interactive browser sign-in (Authorization Code + PKCE) is served by the server
itself at `/auth/login`; it is not part of this SDK.

## Jobs and SSE progress

- `jobs.create(goal, run_at=None)` - enqueue; `run_at` accepts a `datetime` or
  ISO 8601 string. Workers claim due jobs and retry failures up to the server's
  per-job `max_attempts` (3).
- `jobs.get(job_id)` - full state. `job.result` is the server's JSON string;
  `job.result_data` decodes it. `job.is_terminal` is True for
  done/failed/cancelled.
- `jobs.cancel(job_id)` - queued jobs cancel immediately; running jobs move to
  `cancel_requested` and stop cooperatively between agent steps. Cancelling an
  already-cancelled job is an idempotent no-op; `ConflictError` is raised only
  when the job is done or failed.
- `jobs.events(job_id, after=N)` / `jobs.iter_events(job_id)` - the durable,
  cursor-based event log.
- `jobs.stream_events(job_id, after=N)` - live SSE. Reconnects automatically
  with `Last-Event-ID`, so events are never duplicated or skipped; heartbeats
  go to the `on_heartbeat` callback. The server closes the stream itself only
  after done/failed; for a cancelled job the SDK closes the stream client-side
  when the `cancelled` event arrives (the server would otherwise heartbeat
  forever - this mirrors the server contract deliberately).
- `jobs.wait(job_id, timeout=None, poll_interval=2.0)` - simple blocking poll
  to a terminal state; raises `WaitTimeoutError` on deadline.

## Errors, retries, rate limits

All HTTP errors raise typed subclasses of `ApiError`:

| Status | Exception | Extra attributes |
| --- | --- | --- |
| 400 | `BadRequestError` | |
| 401 | `AuthenticationError` | `www_authenticate` |
| 403 | `PermissionDeniedError` | `missing_scope` |
| 404 | `NotFoundError` | |
| 409 | `ConflictError` / `IdempotencyConflictError` | typed subclass for reused key with a different payload |
| 422 | `ValidationError` | `issues` (FastAPI issue list when present) |
| 429 | `RateLimitError` | `retry_after` |
| 5xx | `ServerError` | |

Every `ApiError` carries `status_code`, `request_id` (from `X-Request-ID` or
the 500 body), and the raw `detail`. Transport failures raise `NetworkError`;
SSE failures raise `StreamError`.

Retries follow the server's own transport semantics: bounded attempts (3),
jittered exponential backoff, `Retry-After` honoured on 429/503, and transient
statuses only (408/429/5xx). Only idempotent methods (`GET`, `HEAD`, `DELETE`)
are retried automatically. Queued-job creation accepts `idempotency_key=` for safe
caller-directed retries; synchronous runs remain non-idempotent. Tune with
`MeemeeClient(..., retry=RetryPolicy(...))`.

Rate-limit headers on every response are exposed as `client.last_response_info`
(`request_id`, plus `rate_limit.limit/remaining/reset`). `/health` and `/ready`
are limiter-exempt server-side.

## Verified, Thin, Missing

**Verified (262 tests: 134 sync tests against a mocked transport implementing
the server v0.122.0 contract, 27 async, 4 introspection, 18 async-OIDC and 12
401-refresh tests against mocked transports, 23 WebSocket tests against a
scripted loopback WebSocket server, plus 44 live integration tests that boot
the real server package and exercise it end to end: 5 of them against a local
HTTPS OIDC issuer, and 14 agent-run tests (7 in SQLite mode, 7 in PostgreSQL
mode, which skip without `MEEMEE_TEST_POSTGRES_DSN`) against a booted server,
worker and scripted model provider):**

1. Auth header attachment, 401/403 mapping including `WWW-Authenticate` and
   `missing_scope` extraction (mocked + live).
2. OIDC discovery, client-credentials fetch, caching, leeway refresh, forced
   refresh, scope pass-through, and every token-endpoint failure mode.
3. Runs: request shape, report parsing, 502 mapping, goal bounds (2-20000).
4. Jobs: create with/without `run_at` (ISO 8601 validation), get with
   JSON-string result decoding, cancel statuses, 404/409 mapping, and the
   live-verified idempotent re-cancel (200, current status) for
   already-cancelled jobs.
5. Event log cursors (`after`) and full drain via `iter_events` (mocked + live).
6. SSE: WHATWG parser (multi-line data, comments, CRLF, chunk splits, id
   persistence, NUL rejection), live streaming, automatic `Last-Event-ID`
   resume after mid-stream drops, reconnect budget, server error frames,
   malformed frames, heartbeat callback, and the client-side close on
   `cancelled` - verified against a real server stream, which heartbeats
   forever on cancelled jobs.
7. Tokens: sorted scope posts, local allowlist validation, one-time reveal,
   revoke with immediate effect (live).
8. Audit: cursor pagination, chain fields, limit bounds (1-500), real
   server-verified chain containing the session's own token/job entries (live).
9. Retry policy: `Retry-After` honoured and capped, jittered backoff bounds,
   POST never auto-retried, permanent errors fail fast, network-error retries.
10. Rate-limit and request-id metadata capture from the real limiter (live).
11. `wait()` polling to terminal states with timeout.
12. Companion: user upsert/get/list, persona and check-in updates, fact CRUD
    and search, chat turns with local validation, conversations and message
    history, check-in planning/listing and the admin delivery tick, all
    parsed into typed models.
13. Companion live: the same companion surface exercised against a booted
    server, including chat turns with server-side fact extraction, idempotent
    check-in planning and companion scope enforcement (live).
14. `AsyncMeemeeClient`: every resource (runs, jobs, tokens, audit, quota,
    approvals, webhooks, companion) with identical request shapes and path
    encoding, error mapping for every status, Retry-After-honouring GET
    retries, POST never retried unless keyed, async cursor pagination, async
    SSE with `Last-Event-ID` resume, heartbeats and client-side close on
    `cancelled`, `wait()` timeouts, native async and thread-offloaded auth
    providers, and 20 concurrent requests on one client (mocked + live).
15. WebSocket job streaming (sync and async): bearer handshake, ordered
    delivery, resume with `?after` after an abrupt TCP drop or a 1011 close,
    duplicate suppression, 44xx and HTTP handshake rejections as typed errors
    without reconnect, 5xx handshake retry, malformed-frame errors, reconnect
    budget and backoff, connection refused, and socket close when the consumer
    stops early (scripted server); replay, resume, following a job while it is
    cancelled, and 4401/4403/4404 rejections over real sockets (live).
16. Token introspection, `tokens.introspect(raw_token)` (sync and async):
    the secret is sent only in the POST body, never the URL; active, revoked,
    expired, unknown and bootstrap states parse into `TokenIntrospection`;
    introspection leaves `last_used_at` untouched while real use updates it;
    non-admin callers get `PermissionDeniedError(missing_scope="admin")`; the
    audit chain records the call without the secret (mocked + live).
17. `AsyncOIDCClientCredentialsAuth`: discovery, explicit token endpoint,
    client_secret_basic, scope pass-through, caching with leeway refresh,
    forced refresh, single-flight fetch for concurrent callers, every
    discovery/token failure mode with the sync provider's error types, no
    thread offload when used by AsyncMeemeeClient, TypeError on the sync
    client, and the thread-offload fallback for the sync provider (mocked).
    Live: a local HTTPS issuer mints RS256 JWTs that the booted server
    validates via JWKS; concurrent requests share one fetch, a refreshed token
    is accepted and introspects as `oidc`, a wrong client secret, an unmapped
    role and a forged signature are all rejected.
18. 401 refresh-and-retry-once (sync and async): with a provider exposing
    `refresh()` (both OIDC providers; sync or async `refresh`), a 401 forces one
    refresh and one retry of the same request, including POST bodies rebuilt
    from their JSON and SSE/WebSocket streams reopened from scratch; a second
    401 raises `AuthenticationError`; the refresh does not use a retry attempt
    or the stream reconnect budget; providers without `refresh()` (such as
    `TokenAuth`) keep the single attempt; a failing refresh propagates (mocked;
    live with a revoked-then-valid token over HTTP, SSE and WebSocket, and with
    OIDC providers whose cached token the server rejects).
19. Live agent runs (sync and async, `tests/test_live_runs.py`): `runs.create`
    against a booted server whose model is a local scripted OpenAI-protocol
    provider parses the final answer, step count and the real
    `workspace.read_file` tool result; `runs.get/list/iter_all` return the
    stored run (created_at stamped by the store) with cursor paging; other
    owners get `NotFoundError` and a token without `runs:write` gets
    `PermissionDeniedError`; a queued job runs in `meemee worker` and streams
    over SSE and WebSocket from `queued` to `done` with `result_data` parsed;
    a dead model endpoint raises `ServerError` 502 with `agent run failed`
    and no automatic retry. Runs in SQLite and PostgreSQL modes. `Job.result`
    accepts the PostgreSQL store's decoded object as well as SQLite's string.

The live suite lives in `tests/test_live_integration.py`; it boots uvicorn
against the `meemee` package next to `sdk/` and skips cleanly when that
package or its dependencies are absent.

**Thin:** nothing. Every SDK behaviour above is implemented and tested at its
stated boundary.

**Missing, not claimed:**

- A live `POST /v1/runs` test against a real hosted model. The SDK run path is
  verified live in `tests/test_live_runs.py` against a booted server and worker
  with a local scripted OpenAI-compatible provider, not a hosted model.
- Interactive OIDC browser login - owned by the server's `/auth/login`.
- Self-service introspection for non-admin callers - `/v1/tokens/introspect`
  is admin-only. Customers see their own tokens through the account token list.
