# meemee-client

Typed Python SDK for the [Meemee](../README.md) agent platform API. Targets the
server **v0.79.0** HTTP contract: scoped API tokens and OIDC bearer auth,
synchronous runs, durable queued jobs, resume-safe SSE progress, fixed-window
rate limiting, and the tamper-evident audit chain.

Python 3.10+. Dependencies: `httpx`, `pydantic` v2. Nothing else.

## Install

```bash
pip install -e ./sdk          # from the repository root
pip install -e './sdk[dev]'   # with pytest for the test-suite
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
| 409 | `ConflictError` | |
| 422 | `ValidationError` | `issues` (FastAPI issue list when present) |
| 429 | `RateLimitError` | `retry_after` |
| 5xx | `ServerError` | |

Every `ApiError` carries `status_code`, `request_id` (from `X-Request-ID` or
the 500 body), and the raw `detail`. Transport failures raise `NetworkError`;
SSE failures raise `StreamError`.

Retries follow the server's own transport semantics: bounded attempts (3),
jittered exponential backoff, `Retry-After` honoured on 429/503, and transient
statuses only (408/429/5xx). Because the server has no idempotency keys, only
idempotent methods (`GET`, `HEAD`, `DELETE`) are retried automatically - a
retried `POST /v1/runs` or `/v1/jobs` could execute the goal twice. Tune with
`MeemeeClient(..., retry=RetryPolicy(...))`.

Rate-limit headers on every response are exposed as `client.last_response_info`
(`request_id`, plus `rate_limit.limit/remaining/reset`). `/health` and `/ready`
are limiter-exempt server-side.

## Verified, Thin, Missing

**Verified (107 tests: 96 against a mocked transport implementing the server
v0.79.0 contract, plus 11 live integration tests that boot the real server
package and exercise it end to end):**

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

The live suite lives in `tests/test_live_integration.py`; it boots uvicorn
against the `meemee` package next to `sdk/` and skips cleanly when that
package or its dependencies are absent.

**Thin:** nothing. Every SDK behaviour above is implemented and tested at its
stated boundary.

**Missing, not claimed:**

- An async (`asyncio`) client - the SDK is synchronous only.
- A live test of `POST /v1/runs` - a real run needs a reachable model
  endpoint; runs are covered in the mocked suite only.
- WebSocket streaming - the server offers resume-safe SSE only.
- Idempotency keys for safe POST retries - the server does not support them.
- Interactive OIDC browser login - owned by the server's `/auth/login`.
- Token introspection/listing and job listing endpoints - the server does not
  expose them (token admin is create/revoke; jobs are addressed by id).
