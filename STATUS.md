# Meemee status

**Current core version:** 0.122.0  
**Ledger:** 163 verified, 0 thin  
**Supported production shape:** SQLite/WAL single-host; PostgreSQL memory and owner-scoped jobs are selectable but require target-environment live verification  
**Last core verification (main after pb7 841a3b1 merge, on 1eda8b6):** 473 core + PostgreSQL passed (1 skipped: opt-in live HF router check), including all live PostgreSQL 16 e2e and `tests_pg`; 266 SDK passed including live PostgreSQL SDK runs; core Ruff clean  

## What is production-usable now

The authoritative detailed list is the 123-item "Verified" section in [README.md](README.md). The main product surfaces are:

- Bounded agent loop, typed tools, per-run and persistent exact-tool approvals, deterministic policy, secret scrubbing and safe provenance.
- Local and scheduled jobs, atomic claims, retries, cooperative cancellation, resumable SSE and idempotent submission.
- SQLite/WAL persistence, backups, checksummed migrations, retention, quotas and shared single-host rate limits.
- Scoped/revocable API tokens with admin introspection, OIDC bearer validation, interactive OIDC login and tamper-evident audit.
- Browser automation, safe workspace I/O, allowlisted command execution, local Git inspection/commit and GitHub repository scouting.
- Metrics, JSON logs, dependency readiness, request IDs, graceful shutdown, Docker, Kubernetes manifests and operator documentation.
- Customer application at `/app/`, operator console at `/console/`, and the additive `meemee-client` Python SDK (sync and asyncio clients; SSE and WebSocket job streaming).
- Companion layer: persistent per-user profiles with validated persona configuration, durable provenanced fact memory with full-text retrieval, per-channel conversation persistence, a persona-conditioned conversational engine with bounded fact extraction, proactive check-ins with timezone-aware quiet hours on a durable delivery queue, and local/webhook/WhatsApp/iMessage channel adapters (provider channels config-gated), exposed over scoped HTTP APIs and the `meemee companion` CLI.

## Verification evidence

| Surface | Latest evidence in this tree | Result |
|---|---|---|
| Core server/runtime | `pytest -q` at v0.103.0 | 291 passed |
| Core lint | `ruff check meemee tests examples/webhook_receiver` after v0.100.0 | clean |
| SDK | branch pb4 local run with `websockets` installed | 262 passed, including 44 live-server tests (async client, WebSocket streaming, token introspection, async OIDC against a local HTTPS issuer, 401 refresh-and-retry, and 14 agent-run tests against a booted server + worker + scripted provider in SQLite and PostgreSQL modes) |
| PostgreSQL package | contract run at v0.103.0 | 5 contract tests passed; 2 live tests skipped without `MEEMEE_TEST_DATABASE_URL` |
| Console | core mount test plus console worker's headless live test | passed at merge |
| Structured refusals (pb7) | `tests/test_approval_refusals.py`, SDK `tests/test_models.py`, live refusal test in `tests/test_live_runs_e2e.py` | 5 unit + 3 SDK model tests; live SQLite and PostgreSQL 16.2: runs, stored runs, run list, jobs and SDK parse `approvals_required`; suggested bodies work unchanged |
| Blocked flag (pb7) | `tests/test_approval_refusals.py`, SDK `tests/test_models.py`, `tests/test_live_runs_e2e.py::test_blocked_flag_on_runs_jobs_and_sdk` | 2 unit + 1 SDK model test; live SQLite and PostgreSQL 16.2 (UTC and America/Los_Angeles): runs, stored runs, run list, job get/list (status=done), unfinished job, SDK parse; PG job `result` wire shape fixed to a JSON string |
| Webhook tenancy + live delivery (pb7) | `tests/test_webhook_private_override.py`, `tests/test_live_runs_e2e.py::test_signed_job_webhooks_reach_a_real_receiver_retry_and_stay_in_tenant` | SECURITY FIX: job events went to every principal's webhooks, now owner-only (live test fails on the old code). 9 unit tests (override on/off, HTTPS still required, production refusal incl. API and webhook-worker startup, owner-only fan-out). Live over TLS to a local receiver with SQLite and PostgreSQL 16.2 job stores: `job.done` payload with `blocked`, signature verify/negative cases, 500 then retry with same delivery ID, attempt timeline, second principal receives nothing |
| Write-approval gate (pb7) | `tests/test_live_runs_e2e.py` approval tests, booted server + worker | SQLite and PostgreSQL 16.2: 10 passed (deny, per-run allow, grant constraints/scope/expiry/revoke with audit order, unknown-tool grant rejected, jobs honor grants) |
| End-to-end run path (pb7) | `tests/test_live_runs_e2e.py` against booted uvicorn + `meemee worker`, local scripted OpenAI-compatible provider | SQLite and PostgreSQL 16.2 modes: 10 passed (sync run, owner scoping, queued job over SSE to done, replay, routed fallback, 502 on model outage) |
| Live PostgreSQL (pb7) | `python scripts/pg_live_check.py` on local PostgreSQL 16.2 | UTC server: 31 passed, 0 skipped. Asia/Kolkata server: 29 passed, 2 failed (audit verify timezone bug; fixed on branch pb4, 31 passed with that fix applied) |
| Forced tool interruption (pb7) | `pytest -q` on branch pb7 | 358 passed (12 new in `tests/test_isolation.py`); ruff clean |
| Concurrency regression | `tests/test_concurrency.py` | 2,000 parallel token auths and 1,000 parallel audit appends passed |

Counts are evidence from the named run, not a promise that unrun optional infrastructure works. Run every relevant suite in your target environment before release.

## Thin (0)

Nothing is classified as thin. A capability is either verified at a stated boundary or listed below.

## Missing, not claimed

- Live WhatsApp/iMessage delivery over real provider networks: adapters and the delivery queue are implemented and config-gated, but no provider account exists to verify against.
- External OIDC remains optional.
- Browser takeover across processes: live sessions are held by the API server process; `meemee run` (CLI) and separate `meemee worker` processes do not get the session tools, and open sessions are marked `lost` on restart.
- Live WhatsApp/iMessage delivery over real provider networks: adapters and the delivery queue are implemented and config-gated, but no provider account exists to verify against. Console screens for the companion layer are not built; the surface is HTTP API, CLI, SDK and the console companion view.
- Live PostgreSQL test run against a managed or HA production server (network TLS, replicas). A live run on local PostgreSQL 16.2 is verified on branch pb7 (see evidence table). Checksummed SQLite-to-PostgreSQL copy/import tooling is implemented.
- Built-in account deletion covers customer identity and companion content; product-wide jobs/runs remain covered by the existing portable export and retention tools rather than immediate destructive deletion. External OIDC remains optional.
- Interactive browser human takeover after explicit challenge detection/handoff.
- Forced interruption for tools that are not marked for process isolation or cannot be pickled; those still rely on cooperative cancellation. Opt-in isolated tools are force-killable (branch pb7, `meemee/isolation.py`).
- Multi-region failover, online dual-write database migration and logical replication.

- Approval is decided up front only. There is no pause-for-approval and resume: an unapproved write tool is refused and the run continues or ends. Refusals are reported in `approvals_required` with the body that would allow them, but acting on one means re-running the goal.
- Queued jobs cannot carry per-request approvals, and granting requires the admin scope, so a non-admin user cannot approve a write for their own job.
- Persistent approvals live in `approvals.sqlite3` on the host even in PostgreSQL mode, so API and workers on different hosts do not share grants.
- Webhook outbox and subscriptions stay in `webhooks.sqlite3` on the host in PostgreSQL mode too, so the live webhook test's PostgreSQL run exercises the PostgreSQL job store with the SQLite outbox. The SSRF check runs when a URL is subscribed, not again at delivery, so a hostname that later re-resolves to a private address (DNS rebinding) is not re-checked.

## Release gate

Before calling a release ready:

1. Run core lint and tests.
2. Run SDK tests, including the live-server suite.
3. If PostgreSQL is in scope, set `MEEMEE_TEST_DATABASE_URL` and run all `tests_pg` without skips.
4. Run the benchmark/load harness and require zero unexpected 5xx responses.
5. Open `/console/`, verify login, job creation, SSE progress, cancellation and audit verification.
6. Create and verify a backup, then rehearse restore on a separate instance.
7. Check README, STATUS and CHANGELOG version/counts in the same change.

- Email bridge: outbound task delivery is implemented through verified Resend configuration. Gmail reply ingestion is implemented but blocked on OAuth connection and has no live-read evidence yet.

- Model layer (0.118.0): named profiles, per-role routing with fallback, Inkling via the HF router (needs a free HF token; metered after HF's small monthly free credits), self-hosted Inkling-Small plans with hardware-floor checks, optional paid Sakana Fugu. A live Inkling completion has not been run from this environment because no HF token is configured here; the unauthenticated live router listing of Inkling-Small is verified by an opt-in test (`MEEMEE_LIVE_HF=1`). Self-hosted Inkling has not been run on real hardware (needs 100GB+ RAM+VRAM minimum).

- Models health endpoint (0.120.0): `GET /v1/models/status[?probe=true]`.
- Account deletion: this tree deletes companion content (including model traces). Personal-model and context-store deletion lands when the pb4 branch is merged into main; it is not claimed here until then.

- Phase B follow-ons (0.119.0): per-turn model audit trail on companion replies and a scheduled reflection worker on the reflection route.

- Phase B personal-model v1 is complete: typed claims, evidence, conflicts/supersession, temporal expiry, bounded reflection, user correction, evidence view and confidence decay are implemented and tested.

- Browser human takeover (branch pb1): implemented and tested with real Chromium - 8 tests in `tests/test_browser_takeover.py` plus a manual booted-server run where a person solved a challenge through `/browser/takeover` and the session returned to the agent with outcome `completed`.
- Account deletion (branch pb4, unreleased): product-wide and resumable. Covers jobs/events, runs, agent memory, personal model, context, monitors, webhooks, quotas, plans, idempotency, tool approvals and companion content for built-in accounts (`DELETE /v1/account`), external principals (admin `DELETE /v1/admin/principals/{id}/data`) and operators (`meemee account-delete`). Evidence: `tests/test_account_deletion.py` 10 passed; `tests_pg/test_account_purge_pg.py` 2 passed against a live PostgreSQL 16 server; full core suite 354 passed including both wheel builds. Audit log retained by design.
- Live PostgreSQL run (pb4, 2026-09-24): all 12 `tests_pg` tests pass with `MEEMEE_TEST_POSTGRES_DSN` set, after fixing `AuditLog.verify` to hash `occurred_at` in UTC (it failed on any server whose timezone is not UTC).
- Companion console screens (branch pb4, unreleased): profile editor, persona editor, facts list with retire, conversations browser across channels, check-in queue with status filter and admin delivery run. Evidence: `tests/test_console_companion_browser.py` drives headless Chromium against a live uvicorn server and passes.

