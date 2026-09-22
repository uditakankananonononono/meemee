# Meemee status

**Current core version:** 0.66.0  
**Ledger:** 90 verified, 0 thin  
**Supported production shape:** one host, one or more API/worker processes, SQLite/WAL on local persistent storage  
**Last core verification:** 190 passed; 112 SDK passed; Ruff and explicit 0.66.0 release audit clean

## What is production-usable now

The authoritative detailed list is the 90-item "Verified" section in [README.md](README.md). The main product surfaces are:

- Bounded agent loop, typed tools, per-run and persistent exact-tool approvals, deterministic policy, secret scrubbing and safe provenance.
- Local and scheduled jobs, atomic claims, retries, cooperative cancellation, resumable SSE and idempotent submission.
- SQLite/WAL persistence, backups, checksummed migrations, retention, quotas and shared single-host rate limits.
- Scoped/revocable API tokens, OIDC bearer validation, interactive OIDC login and tamper-evident audit.
- Browser automation, safe workspace I/O, allowlisted command execution, local Git inspection/commit and GitHub repository scouting.
- Metrics, JSON logs, dependency readiness, request IDs, graceful shutdown, Docker, Kubernetes manifests and operator documentation.
- Operator console at `/console/` and the additive `meemee-client` Python SDK.

## Verification evidence

| Surface | Latest evidence in this tree | Result |
|---|---|---|
| Core server/runtime | `pytest -q` after v0.66.0 | 190 passed |
| Core lint | `ruff check meemee tests examples/webhook_receiver` after v0.66.0 | clean |
| SDK | v0.66.0 full run | 112 passed, including 11 against a booted server |
| PostgreSQL package | last combined run at v0.22.0 | 3 contract tests passed; 2 live tests skipped without `MEEMEE_TEST_DATABASE_URL` |
| Console | core mount test plus console worker's headless live test | passed at merge |
| Concurrency regression | `tests/test_concurrency.py` | 2,000 parallel token auths and 1,000 parallel audit appends passed |

Counts are evidence from the named run, not a promise that unrun optional infrastructure works. Run every relevant suite in your target environment before release.

## Thin (0)

Nothing is classified as thin. A capability is either verified at a stated boundary or listed below.

## Missing, not claimed

- Full queued-job/API composition-root selection of the additive PostgreSQL stores. Agent-memory selection is wired; the API and worker job surfaces still instantiate SQLite stores.
- Automated SQLite-to-PostgreSQL copy/cutover tooling and a live PostgreSQL test run in this environment.
- Account administration UI. Authentication, sessions and scoped tokens exist; identity lifecycle remains at the IdP.
- Interactive browser human takeover after explicit challenge detection/handoff.
- Cross-host rate limiting until the core runtime selects a shared backend.
- Forced interruption inside blocking third-party native code; async tools are interruptible.
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
