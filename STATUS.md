# Meemee status

**Current core version:** 0.121.0  
**Ledger:** 162 verified, 0 thin  
**Supported production shape:** SQLite/WAL single-host; PostgreSQL memory and owner-scoped jobs are selectable but require target-environment live verification  
**Last core verification:** 308 passed; 133 SDK passed (15 live skipped); PostgreSQL suite 8 passed, 2 live skipped; Ruff, package audit and explicit 0.112.0 release audit clean

## What is production-usable now

The authoritative detailed list is the 123-item "Verified" section in [README.md](README.md). The main product surfaces are:

- Bounded agent loop, typed tools, per-run and persistent exact-tool approvals, deterministic policy, secret scrubbing and safe provenance.
- Local and scheduled jobs, atomic claims, retries, cooperative cancellation, resumable SSE and idempotent submission.
- SQLite/WAL persistence, backups, checksummed migrations, retention, quotas and shared single-host rate limits.
- Scoped/revocable API tokens, OIDC bearer validation, interactive OIDC login and tamper-evident audit.
- Browser automation, safe workspace I/O, allowlisted command execution, local Git inspection/commit and GitHub repository scouting.
- Metrics, JSON logs, dependency readiness, request IDs, graceful shutdown, Docker, Kubernetes manifests and operator documentation.
- Customer application at `/app/`, operator console at `/console/`, and the additive `meemee-client` Python SDK.
- Companion layer: persistent per-user profiles with validated persona configuration, durable provenanced fact memory with full-text retrieval, per-channel conversation persistence, a persona-conditioned conversational engine with bounded fact extraction, proactive check-ins with timezone-aware quiet hours on a durable delivery queue, and local/webhook/WhatsApp/iMessage channel adapters (provider channels config-gated), exposed over scoped HTTP APIs and the `meemee companion` CLI.

## Verification evidence

| Surface | Latest evidence in this tree | Result |
|---|---|---|
| Core server/runtime | `pytest -q` at v0.103.0 | 291 passed |
| Core lint | `ruff check meemee tests examples/webhook_receiver` after v0.100.0 | clean |
| SDK | v0.103.0 local run | 133 passed; 15 live-server tests skipped |
| PostgreSQL package | contract run at v0.103.0 | 5 contract tests passed; 2 live tests skipped without `MEEMEE_TEST_DATABASE_URL` |
| Console | core mount test plus console worker's headless live test | passed at merge |
| Forced tool interruption (pb7) | `pytest -q` on branch pb7 | 358 passed (12 new in `tests/test_isolation.py`); ruff clean |
| Concurrency regression | `tests/test_concurrency.py` | 2,000 parallel token auths and 1,000 parallel audit appends passed |

Counts are evidence from the named run, not a promise that unrun optional infrastructure works. Run every relevant suite in your target environment before release.

## Thin (0)

Nothing is classified as thin. A capability is either verified at a stated boundary or listed below.

## Missing, not claimed

- Live WhatsApp/iMessage delivery over real provider networks: adapters and the delivery queue are implemented and config-gated, but no provider account exists to verify against. Console screens for the companion layer are not built; the surface is HTTP API, CLI, SDK and the console companion view.
- Live PostgreSQL test run in this environment. Checksummed SQLite-to-PostgreSQL copy/import tooling is implemented.
- External OIDC remains optional.
- Browser takeover across processes: live sessions are held by the API server process; `meemee run` (CLI) and separate `meemee worker` processes do not get the session tools, and open sessions are marked `lost` on restart.
- Forced interruption for tools that are not marked for process isolation or cannot be pickled; those still rely on cooperative cancellation. Opt-in isolated tools are force-killable (branch pb7, `meemee/isolation.py`).
- Multi-region failover, online dual-write database migration and logical replication.

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

