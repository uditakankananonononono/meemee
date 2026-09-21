# Changelog

All entries describe shipped repository behavior. Missing work is never presented as completed.

## 0.25.2

- Moved graceful shutdown to FastAPI lifespan context.
- Drains active immediate runs before closing pooled model transport.
- Core verification: 92 tests passed; Ruff clean.

## 0.25.1

- Fixed concurrent `TokenStore.authenticate` commits on one SQLite connection.
- Locked audit append/read/verify operations and added busy timeouts.
- Added 2,000-auth and 1,000-audit-operation thread-load regressions.

## 0.25.0

- Added bounded graceful shutdown and immediate-run draining.

## 0.24.0

- Added inbound credential scrubbing before planning, model calls, reports and memory.

## 0.23.0

- Added recursive secret-safe tool provenance and bounded large-output recording.

## 0.22.0

- Mounted the operator console at `/console/`.
- Merged additive Python SDK and PostgreSQL persistence package.
- Made model readiness optional by default while still reporting model health.

## 0.21.1

- Made repeated cancellation explicitly idempotent.
- Fixed SSE streams to close after a cancelled event.

## 0.21.0 and earlier

Introduced the agent/tool core, GitHub scout, memory, planning, API/CLI, browser automation, jobs/workers, authentication/OIDC, audit, backups/migrations, observability, interactive login, rate limits, SSE, cancellation, model retries, policy, idempotency, quotas, retention and commercial operations guide. See README's verified ledger for current boundaries; historical percentages are intentionally omitted.
