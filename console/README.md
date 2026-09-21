# Meemee operator console

A real, static, dependency-free operator console for the Meemee API, served by
the API itself as an additive `console/` directory. No build step, no
framework, no CDN: plain HTML/CSS/ES-module JavaScript that talks only to the
documented v0.21.1 HTTP surface (with graceful degradation against pre-v0.19
servers for readiness and quota endpoints).

## What it does

- **Status** - `/health`, `/ready` (optional 30s auto-refresh), session
  capability probes, a live rate-limit budget readout from the
  `RateLimit-Limit/Remaining/Reset` headers the API attaches to every measured
  response (health/readiness are exempt by design), and an admin-only
  `/metrics` viewer (Prometheus text parsed into a table).
- **Runs** - `POST /v1/runs` with an explicit `approve_writes` opt-in, full
  run-report rendering (final answer, steps used, per-tool arguments, results,
  errors, elapsed ms), browser-local history.
- **Jobs** - `POST /v1/jobs` (immediate or scheduled `run_at`) with a
  per-submission **`Idempotency-Key`** (v0.20+): retries replay the original
  response instead of double-enqueueing, conflicts (409) rotate the key and
  say so. The daily-quota block from the create response is surfaced
  immediately, and quota-exceeded 429s are distinguished from rate-limit 429s.
  Per-job status polling, append-only event log, **live resume-safe SSE
  progress** from `GET /v1/jobs/{id}/stream` (fetch-based, because
  `EventSource` cannot send an `Authorization` header), and
  `DELETE /v1/jobs/{id}` cancellation with the documented 404/409 handling.
- **Quotas** (v0.20+) - `GET /v1/quota` own-principal usage (needs
  jobs:write, labelled when absent) and admin `PUT /v1/quota/{principal}`
  overrides with the audited `quota.update` note.
- **Tokens** - `POST /v1/tokens` with scope checkboxes and optional expiry,
  the one-time secret reveal with copy, and `DELETE /v1/tokens/{id}` revoke.
- **Audit chain** - cursor-paged `GET /v1/audit`, the server-side verification
  outcome surfaced exactly as the API reports it, plus an **independent
  in-browser recomputation** of the whole SHA-256 chain (genesis `0`*64,
  `\x1f`-joined fields, canonical `json.dumps(sort_keys, separators)` metadata)
  using WebCrypto. A successful verification pins the chain head in this
  browser; later loads are checked against that anchor, so a chain that was
  rolled back, replaced, or rewritten below the verified head is flagged in
  red instead of silently accepted. Action/actor summary strip, filters,
  per-entry verified markers, JSON export.
- **Session** - OIDC interactive login (`GET /auth/login`), logout
  (`POST /auth/logout`), or a pasted bearer token (bootstrap, `mee_…` scoped,
  or OIDC access token) kept in sessionStorage by default.

## Wiring (main builder)

The console is served by mounting this directory on the existing FastAPI app.
Either two lines in `meemee/api.py`:

```python
from fastapi.staticfiles import StaticFiles
from pathlib import Path

app.mount(
    "/console",
    StaticFiles(directory=Path(__file__).resolve().parent.parent / "console", html=True),
    name="console",
)
```

or, when the repo root is importable:

```python
from console.mount import mount_console
mount_console(app)
```

Then open `/console/`. The console uses the same origin for API calls, so the
`meemee_session` cookie works and no CORS setup is required. After OIDC
sign-in, `/auth/callback` redirects to `/` (core behaviour in
`meemee/web_login.py`); navigate to `/console/` from there. Serving from a
different origin would need CORS middleware, which the API does not
configure - same-origin mounting is the supported shape.

## Honest gaps (labelled "Missing" in the UI)

These do not exist in the documented v0.16.0 API; the console says so where it
matters instead of faking data:

- **No `GET /v1/tokens`** - tokens cannot be listed. The console keeps a
  browser-local registry of tokens it created (metadata only, never the
  secret) and revokes by ID.
- **No `GET /v1/jobs`** - jobs cannot be listed. The console tracks jobs
  created or added by ID in this browser and polls each one.
- **No `GET /v1/runs` or `/v1/runs/{id}`** - run history is browser-local.
- **No `/v1/whoami`** - capabilities are inferred with side-effect-free probes
  (a validation-failed 422 proves the scope; 401/403 deny it).
- **No per-entry verification endpoint** - the server verifies the chain as a
  whole on every `/v1/audit` call; the console adds its own full
  recomputation in the browser as a second opinion.
- **SSE terminal coverage** - v0.21+ servers terminate the stream on
  `done`/`failed`/`cancelled`; the console additionally closes client-side on
  any terminal event, which also keeps older servers honest.
- **Client verification numerics** - the canonical encoder matches CPython for
  strings, booleans, null, integers and nested lists/objects. A
  non-integral float inside audit metadata would encode differently
  (`1.0` vs `1`); no current API writer produces one, and a mismatch would
  surface as a verification failure to investigate, never silently pass.

## Security notes

- All API data is rendered with `textContent`, never `innerHTML`.
- The bearer token lives in sessionStorage unless the operator explicitly
  opts into localStorage; it is only ever sent to the configured API origin.
- No external requests: the console loads nothing from CDNs or third parties.
- Token secrets are never persisted; the registry stores id/name/scopes only.
