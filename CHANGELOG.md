# Changelog

All entries describe shipped repository behavior. Missing work is never presented as completed.

## 0.38.0

- Added deterministic `meemee release-audit` CI/release gating for required assets, version drift and explicit stub markers, with structured findings.
- Added the previously missing complete proprietary license file.
- Core verification: 128 tests passed; Ruff and release audit clean.

## 0.37.0

- Added secure, non-destructive `meemee init` onboarding for a commercial first-run path.
- Generates required credentials into an owner-only env file, creates an owner-only data directory, uses exclusive creation to prevent race overwrites, redacts generated values from output, and prints exact preflight/start commands.
- Core verification: 126 tests passed; Ruff clean.

## 0.36.0

- Added `meemee preflight` as a production/CI deployment gate with structured checks for required credentials, storage safety and integrity, model reachability and the supported deployment boundary.
- Keeps optional local model downtime warning-only by default; `--require-model` makes it a hard failure.
- Core verification: 123 tests passed; Ruff clean.

## 0.35.0

- Encrypt webhook signing secrets at rest with the existing required vault key using per-record AES-256-GCM nonces and subscription-bound authenticated context.
- Automatically upgrade legacy plaintext webhook secrets transactionally; fail closed on a missing or wrong key; keep create/rotation one-time reveal behavior.
- Core verification: 120 tests passed; Ruff clean.

## 0.34.0

- Added receiver signature verification helper/CLI, safe custom headers and receiver fixture.
- Core verification: 116 tests passed; Ruff clean.

## 0.33.0

- Added versioned webhook envelope, payload ceiling, field selection and body hashes.
- Core verification: 113 tests passed; Ruff clean.

## 0.32.0

- Added webhook breaker cooldown, success/queue/suspension metrics and alert guidance.
- Core verification: 111 tests passed; Ruff clean.

## 0.31.0

- Added webhook pause/resume, health summaries and attempt timelines.
- Added automatic suspension after five terminal delivery failures.
- Core verification: 108 tests passed; Ruff clean.

## 0.30.0

- Added stale dispatcher-lease recovery, webhook secret rotation and test delivery.
- Core verification: 106 tests passed; Ruff clean.

## 0.29.0

- Added delivery list/status and failed-replay APIs with owner isolation and audit.
- Added webhook outbox Prometheus gauges and terminal delivery retention.
- Core verification: 104 tests passed; Ruff clean.

## 0.28.0

- Exposed authenticated webhook subscription/list/delete APIs.
- Added dispatcher worker CLI and end-to-end terminal job event emission.
- Core verification: 102 tests passed; Ruff clean.

## 0.27.0

- Added durable filtered webhook subscriptions and deduplicated delivery outbox.
- Added HMAC-signed HTTPS delivery, SSRF defenses and bounded retries.
- Core verification: 100 tests passed; Ruff clean.

## 0.26.0

- Added per-run exact-tool approvals and persistent per-principal grants with expiry/revocation.
- Added admin approval APIs and audit events.

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
