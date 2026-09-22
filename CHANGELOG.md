# Changelog

All entries describe shipped repository behavior. Missing work is never presented as completed.

## 0.85.0

- Fixed the operator console job inventory copy to match the implemented owner-scoped server listing.
- Added regression coverage that blocks the obsolete browser-only/no-list-endpoint claim from returning.
- Verification: 216 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, core/SDK wheel builds and explicit 0.85.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.84.0

- Added verified backup restore rehearsal into a new empty data directory.
- Restore verifies source, SQLite integrity and checksums, refuses overwrites and cleans partial writes on failure.
- Verification: 215 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, core/SDK wheel builds and explicit 0.84.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.83.0

- Synchronized console/operator documentation to current stream, account-admin and deployment-image behavior.
- Release audit now blocks legacy v0.46 console and external-account-admin claims.
- Verification: 213 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, core/SDK wheel builds and explicit 0.83.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.82.0

- Fixed stale README verified-count, PostgreSQL evidence and signed-audit retention guidance.
- Release audit now proves the verified heading count equals the numbered capability ledger.
- Verification: 213 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, core/SDK wheel builds and explicit 0.82.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.81.0

- Updated deployment assets and operator guidance for the current SQLite/PostgreSQL boundaries, distributed limiter and checksummed migrations.
- Added missing persistence/audit environment controls and current Kubernetes image pins with regression checks.
- Verification: 212 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, core/SDK wheel builds and explicit 0.81.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.80.0

- Added release-audit guards for core/SDK version drift and stale capability claims.
- Rewrote the Missing ledger to remove obsolete claims while preserving external/environment boundaries exactly.
- Verification: 209 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, core/SDK wheel builds and explicit 0.80.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.79.0

- Added operator-console account administration for assigning plans and validated daily-job quota overrides to IdP-backed principals.
- Mutations require explicit confirmation; identity lifecycle remains correctly owned by the configured IdP.
- Verification: 208 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, core/SDK wheel builds and explicit 0.79.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.78.0

- Added real Python SDK wheel verification for core-version parity, required modules and PEP 561 `py.typed` metadata.
- Removed a stale SDK README test-count claim.
- Verification: 207 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, core/SDK wheel builds and explicit 0.78.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.77.0

- Fixed commercial wheel packaging to include the PostgreSQL runtime package and bundled SQL migrations.
- Package audit and a real wheel-build regression now block missing PostgreSQL assets.
- Verification: 206 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, real wheel build and explicit 0.77.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.76.0

- Added cancellation propagation through delegated agent teams and delegate tools.
- New child work is refused after cancellation; active children receive the shared signal through their agent/tool stack.
- Verification: 205 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff and explicit 0.76.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.75.0

- Hardened readiness disk probing for not-yet-created data directories and disk-stat failures.
- Readiness now returns a structured not-ready component instead of crashing on disk inspection errors.
- Verification: 203 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff and explicit 0.75.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.74.0

- Added PostgreSQL agent-memory contract parity for scrubbed writes and hybrid/semantic retrieval calls.
- PostgreSQL uses ranked native full-text fallback so agent runs work without an undeclared pgvector dependency.
- Verification: 202 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff and explicit 0.74.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.73.0

- Added atomic PostgreSQL-backed fixed-window rate limiting shared across hosts/pods and selected automatically with the PostgreSQL backend.
- Existing headers, cleanup cadence and credential/IP identity rules remain compatible.
- Verification: 201 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff and explicit 0.73.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.72.0

- Added PostgreSQL production preflight for required DSN, live connection/pool setup and zero pending migrations.
- Preflight now reports the selected SQLite or PostgreSQL deployment boundary instead of always claiming single-host SQLite.
- Verification: 199 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff and explicit 0.72.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.71.0

- Added backend-neutral persistence lifecycle: one shared API/worker composition, SQLite/PostgreSQL readiness checks, shared worker memory and pooled shutdown.
- Removed duplicate PostgreSQL pools created by independent memory/job composition calls.
- Verification: 197 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff and explicit 0.71.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.70.0

- Added PostgreSQL job-list opaque keyset cursor parity with stable updated-at/UUID ordering and next-cursor envelopes.
- Malformed cursors fail before any database query.
- Verification: 196 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff and explicit 0.70.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.69.0

- Wired API and worker composition to PostgreSQL memory and owner-scoped queued jobs, with automatic ownership migration.
- Added PostgreSQL owner enqueue/get/list contract; opaque job cursor parity remains explicit and fail-closed.
- Verification: 194 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff and explicit 0.69.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.68.0

- Added checksummed SQLite-to-PostgreSQL core-data export and transactional empty-target import CLIs.
- Copy covers memory, jobs/events, token digests and audit while refusing tampered exports or target collisions.
- Verification: 193 core and 112 SDK tests passed; Ruff and explicit 0.68.0 release audit clean.

## 0.67.0

- Aligned the Python SDK package/import version with core at 0.84.0 and removed stale v0.41 contract labels from SDK docs and metadata.
- Added an explicit parity regression check.
- Verification: 191 core and 112 SDK tests passed; Ruff and explicit 0.67.0 release audit clean.

## 0.66.0

- Added Python SDK parity for argument-scoped approval administration and corrected the approval response model to current server fields.
- Principal/tool paths are safely encoded; constraints are validated before requests.
- Verification: 190 core and 112 SDK tests passed; Ruff and explicit 0.66.0 release audit clean.

## 0.65.0

- Added fail-closed SQLite/PostgreSQL persistence composition selection with automatic PostgreSQL migrations and optional dependency packaging.
- Agent memory can select PostgreSQL; API and worker job parity remain explicitly unclaimed until their ownership/event contracts match.
- Verification: 190 core tests passed; Ruff and explicit 0.65.0 release audit clean. SDK remains 110 passed from v0.55.0; no SDK surface changed.

## 0.64.0

- Added cooperative mid-tool cancellation for asynchronous tools with prompt task cancellation and cleanup propagation.
- Cancelled tool results are durably recorded and the agent exits before another model step.
- Verification: 187 core tests passed; Ruff and explicit 0.64.0 release audit clean. SDK remains 110 passed from v0.55.0; no SDK surface changed.

## 0.63.0

- Added authenticated owner-scoped WebSocket job-event streaming with durable cursor replay and terminal closure.
- Token, OIDC and session authentication plus scope/ownership rejection are enforced before acceptance.
- Verification: 186 core tests passed; Ruff and explicit 0.63.0 release audit clean. SDK remains 110 passed from v0.55.0; no SDK surface changed.

## 0.62.0

- Added a complete operator-console permissions view for listing, granting, constraining, expiring and revoking persistent tool approvals.
- Mutations require explicit browser confirmation and use the admin-scoped approval API.
- Verification: 184 core tests passed; Ruff and explicit 0.62.0 release audit clean. SDK remains 110 passed from v0.55.0; no SDK surface changed.

## 0.61.0

- Added argument-scoped persistent tool approvals with exact constraint-subset matching and API/runtime enforcement.
- Grants can now be repository-, branch-, command- or other argument-specific without widening the tool globally.
- Verification: 183 core tests passed; Ruff and explicit 0.61.0 release audit clean. SDK remains 110 passed from v0.55.0; no SDK surface changed.

## 0.60.0

- Added per-run browser domain allowlists and workspace-confined managed download capture.
- Added explicit CAPTCHA/security-challenge detection and human-required handoff signaling without bypass attempts.
- Verification: 181 core tests passed; Ruff and explicit 0.60.0 release audit clean. SDK remains 110 passed from v0.55.0; no SDK surface changed.

## 0.59.0

- Added approval-gated GitHub branch push and pull-request tools using authenticated API mutations.
- Branch pushes require an exact expected remote head and exact target commit, refusing races before mutation.
- Verification: 179 core tests passed; Ruff and explicit 0.59.0 release audit clean. SDK remains 110 passed from v0.55.0; no SDK surface changed.

## 0.58.0

- Added deterministic local semantic embeddings persisted beside memory, cosine retrieval and reciprocal-rank fusion with FTS.
- Agent context now uses hybrid lexical/semantic retrieval with no external embedding service.
- Verification: 176 core tests passed; Ruff and explicit 0.58.0 release audit clean. SDK remains 110 passed from v0.55.0; no SDK surface changed.

## 0.57.0

- Added signed-checkpoint-gated audit prefix pruning with retained cryptographic chain bases and post-prune suffix verification.
- Historical anchors remain verifiable; invalid, wrong-key and stale checkpoints refuse deletion.
- Verification: 174 core tests passed; Ruff and explicit 0.57.0 release audit clean. SDK remains 110 passed from v0.55.0; no SDK surface changed.

## 0.56.0

- Added bounded structured model-driven replanning when evidence invalidates the active plan, with validated replacement DAGs, a three-revision ceiling, feedback and durable provenance.
- Verification: 172 core tests passed; Ruff and explicit 0.56.0 release audit clean. SDK remains 110 passed from v0.55.0; no SDK surface changed.

## 0.55.0

- Added externally storable HMAC-SHA256 audit-chain checkpoints with no-overwrite creation and verification against historical chain heads.
- Added strict wrong-key, checkpoint-tamper and local-chain-tamper detection plus dedicated create/verify CLIs.
- Verification: 170 core and 110 SDK tests passed; Ruff and explicit 0.55.0 release audit clean.

## 0.54.0

- Added a full-ASGI parallel API load gate through the production middleware/auth/routing stack.
- Requires every response to complete with a request ID and zero network errors or unexpected 5xx responses.
- Verified 500 requests at concurrency 32 with all 500 returning 200.
- Verification: 168 tests passed; Ruff, explicit 0.54.0 release audit and 500-request API loadcheck clean.

## 0.53.0

- Added a release-blocking shared-store contention harness across authentication, audit, jobs and entitlements.
- Reports exact operation/write counts, captured error types and audit-chain verification; exits nonzero on mismatch.
- Verified 1,000 operations across 16 worker threads with zero errors or lost job writes.
- Verification: 166 tests passed; Ruff, explicit 0.53.0 release audit and 1,000-op loadcheck clean.

## 0.52.0

- Added stable keyset pagination and next cursors for webhook subscriptions and delivery history.
- Added explicit audit next cursors while preserving the sequence-after contract.
- Added Python SDK audit page and webhook delivery page/iterator helpers.
- Verification: 164 core and 110 SDK tests passed; Ruff and explicit 0.52.0 release audit clean.

## 0.51.0

- Added opaque stable keyset cursors and `next_cursor` envelopes to principal jobs, completed runs and safe token metadata lists.
- Preserved legacy `before` filters and bounded limits; malformed cursors return validation errors.
- Added Python SDK page and `iter_all` helpers.
- Verification: 162 core and 110 SDK tests passed; Ruff and explicit 0.51.0 release audit clean.

## 0.50.0

- Added checksummed component schema/version registry to evolving jobs, webhooks, runs and entitlements databases.
- Added same-version drift and newer-database refusal plus `meemee schema-status` cross-database visibility.
- Existing additive constructor upgrades remain compatible and are registered after successful upgrade.
- Added an explicit expected-version release-audit gate so a stale but internally aligned release number cannot pass packaging.
- Metadata-fix verification: 160 tests passed; Ruff and explicit 0.50.0 release audit clean.

## 0.49.0

- Added checksummed owner-scoped account export for jobs, completed runs and entitlement assignment.
- Added no-write checksum inspection and target-principal remapping.
- Added collision-preflighted import with safety copies, rollback restoration and overwrite refusal.
- Core verification: 157 tests passed; Ruff and release audit clean.

## 0.48.0

- Added hidden-input operator encryption-key rotation across vault records and webhook signing secrets.
- Validates every old-key decryption before writes, makes verified safety backups, blocks startup during interruption and restores all databases on failure.
- Core verification: 154 tests passed; Ruff and release audit clean.

## 0.47.0

- Extended readiness to the durable run and entitlement stores.
- Added configurable completed-run retention with exact deletion reporting.
- Proved verified online backup/restore coverage for run, entitlement, approval and webhook commercial state.
- Core verification: 152 tests passed; Ruff and release audit clean.

## 0.46.0

- Added admin-only safe token metadata listing without digest or raw-credential disclosure.
- Added durable principal-owned completed run history with owner-scoped list/get APIs.
- Updated Python SDK and packaged console token/run views to use server source-of-truth inventory.
- Verification: 150 core and 110 SDK tests passed; Ruff and release audit clean.

## 0.45.0

- Added persisted job ownership and owner-scoped get/cancel/events/SSE/list operations with status and cursor filters.
- Updated the Python SDK and packaged console to list jobs from the server source of truth instead of browser-local history.
- Legacy jobs remain unowned rather than being silently exposed to a principal.
- Verification: 146 core and 110 SDK tests passed; Ruff and release audit clean.

## 0.44.0

- Added exact authenticated `/v1/whoami` identity, scopes, plan limits and live usage.
- Updated the packaged console Status view to render account and plan usage, removing its former whoami gap.
- Core verification: 142 tests passed; Ruff and release audit clean.

## 0.43.0

- Enforced each plan's persistent exact-tool approval limit using active, non-expired grants.
- Added exact current usage for daily jobs, active webhooks and persistent approvals to current entitlements.
- Core verification: 140 tests passed; Ruff and release audit clean.

## 0.42.0

- Added persistent starter/team/business entitlement assignments and a public machine-readable product catalog.
- Enforced plan daily-job limits through quota assignment and active-webhook limits at creation; assignments are admin-only and audited.
- Billing remains explicitly external: no prices, checkout or payment state are claimed.
- Verification: 138 core and 109 SDK tests passed; Ruff and release audit clean.

## 0.41.0

- Corrected OpenAPI authentication to publish standard HTTP Bearer security requirements on protected operations.
- Extended the typed Python SDK to current commercial quota, exact-tool approval and webhook administration/delivery operations, and aligned its release version with the server.
- Updated live SDK integration startup for the required encrypted webhook-secret key.
- Verification: 135 core and 109 SDK tests passed; core/SDK scoped Ruff and release audit clean.

## 0.40.0

- Added trusted-host enforcement, restrictive CSP, permissions/referrer/frame/content-type controls and no-store API/auth caching policy.
- Added operator-controlled HSTS so TLS deployments can enforce transport security without breaking local HTTP defaults.
- Core verification: 133 tests passed; Ruff and release audit clean.

## 0.39.0

- Fixed source-versus-wheel drift by packaging the complete operator console and PEP 561 marker with the core distribution.
- Added `meemee package-audit` for wheel metadata, CLI entry point and required-content verification.
- Proved isolated wheel installation, installed CLI help/init execution and installed console asset import outside the repository checkout.
- Core verification: 130 tests passed; Ruff, release audit, package audit and isolated install smoke clean.

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
