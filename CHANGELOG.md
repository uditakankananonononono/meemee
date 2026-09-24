# Changelog

All entries describe shipped repository behavior. Missing work is never presented as completed.

## 0.121.0

Integrates pb1 (browser human takeover), pb4 (product-wide account deletion, now also purging browser sessions and takeover notices) and pb7 (process-isolated tool kill).

- Added interactive browser human takeover: durable live browser sessions, challenge-triggered or requested takeovers with one-time expiring links, a WebSocket live view that relays clicks, typing, keys, scrolls and in-policy navigation, and hand-back that resumes the agent on the same page.
- Takeover view relays drag gestures (press-move-release) so slider challenges can be solved.
- Added takeover-link notices: sessions opened with `notify_user_id` queue a durable notice delivered on the user's companion check-in channel (local by default); link text is erased after delivery and notices for ended takeovers are cancelled.
- Added agent tools `browser.session_open`, `browser.session_act`, `browser.session_request_human`, `browser.session_wait_human`, `browser.session_snapshot`, `browser.session_close` (registered in the API server process).
- Added `/v1/browser/sessions` open/list/detail/takeover/close endpoints, `/v1/browser/takeover/release`, the `/v1/browser/takeover/ws` socket and the `/browser/takeover` viewer.
- Fixed: `websockets` is now a core dependency. Plain `pip install .` (as in the Dockerfile) previously had no WebSocket support in uvicorn, so WebSocket endpoints could not accept connections.
- Security headers middleware keeps a route-specific Content-Security-Policy instead of overwriting it; all other responses keep the existing default policy.

- Product-wide account deletion: jobs and job events, run reports, agent memories, personal model (including soft-deleted history), connected context, monitors, webhooks, quotas, plans, idempotency records, tool approvals and companion content are hard-deleted for the principal. Audit log retained.
- Running jobs are tombstoned and removed when their worker settles; memories written by the in-flight run are removed too. Late synchronous runs are discarded with HTTP 410.
- Durable deletion ledger with crash resume (`meemee account-delete-resume`, automatic on API start); admin erasure endpoint for external principals; `meemee account-delete PRINCIPAL --yes`.
- PostgreSQL migration 004 adds job purge tombstones; expired tombstones are reaped on claim.
- Fixed PostgreSQL `AuditLog.verify` failing on servers whose timezone is not UTC.
- `Agent.run` accepts an optional caller-supplied `run_id`.
- Added `meemee/isolation.py`: opt-in process-isolated tool execution. Cancellation and a per-tool hard timeout terminate, then kill, the child process, so tools blocked in native code can be stopped.
- `ToolRegistry.execute` honors a new `Tool.isolation` policy; existing tools are unchanged.
- Added 12 regression tests covering results, errors, crashes, timeouts, cancellation, SIGKILL escalation and event-loop responsiveness.

## 0.120.0

- Added `GET /v1/models/status` (scope `jobs:read`): profiles with availability and reason, routes and paid gating, no secrets. `?probe=true` probes routed profiles' `/models` endpoints concurrently and reports the serving profile per role.

## 0.119.0

- Companion replies now include `model_trace` (role, answering profile, model id and every fallback attempt), stored per assistant message in `companion_message_models` and covered by customer export and deletion. The SDK `CompanionChatReply` gains the optional field.
- Added `meemee reflection-worker`: scheduled personal-model reflection on the `reflection` route, gated per owner by new context evidence and `MEEMEE_REFLECTION_INTERVAL_MINUTES`, with per-owner status, model trace and audit events. Failed runs retry on the next due pass without advancing the watermark.

## 0.118.0

- Added named model profiles and per-role routing (`MEEMEE_MODEL_ROUTES`, `MEEMEE_MODEL_PROFILES`) with ordered fallback and per-attempt records. The agent, companion chat and personal-model reflection each resolve their own role. With no routing config, behavior is unchanged (local only).
- Added Inkling via the Hugging Face router (`inkling`, `inkling-large`; `MEEMEE_HF_TOKEN`) and self-hosted Inkling-Small (`inkling-vllm`) with `meemee models inkling-local` hardware checks, exact vLLM/llama.cpp commands and `deploy/inkling/serve-inkling.sh`.
- Added optional Sakana Fugu (paid; requires key and `MEEMEE_ALLOW_PAID_MODELS=true`). "Ultron" is not built in: no single model by that name exists; custom profiles cover any specific checkpoint.
- Added `meemee models list|check`, docs/models.md and deploy/inkling/README.md.

## 0.117.0

- Completed the personal-model v1 with user correction provenance, owner-scoped evidence inspection and confidence decay for stale inferred claims.
- User corrections supersede inferred values at full confidence and are exempt from automated decay.
- Added authenticated correction/evidence APIs, audit events and closeout regressions.

## 0.116.0

- Added a source-grounded reflection worker that proposes typed personal-model claims and rejects any claim whose cited source record is absent from the owner's context ledger.
- Added explicit expiry handling for temporally stale claims and an authenticated audited reflection endpoint.
- Reflection remains bounded to 20 claims per run and forbids unsupported sensitive inference in its model contract.

## 0.115.0

- Added the evidence-backed personal-model foundation for goals, relationships, projects, preferences, routines and constraints.
- Added owner isolation, confidence merging, conflict supersession with retained history, temporal validity, source evidence and scoped deletion.
- Wired the active personal model into companion and agent grounding and added authenticated list/write/delete APIs with audit events.

## 0.114.0

- Added an authenticated email bridge status endpoint and verified-address task-email endpoint using the existing Resend transactional delivery path, including a configured Reply-To address.
- Added a Gmail read-only connector that normalizes inbox messages into the durable owner-scoped context ledger with message/thread provenance and cursor-based dedupe.
- Gmail operation is not claimed until OAuth is connected and a live read succeeds; status returns the exact required scope and connection requirement.

## 0.113.0

- Wired the unified context assembler into companion chat: each turn retrieves only that user's permitted cross-source records and includes content plus provenance in the system grounding.
- Wired unified context into autonomous agent runs so goals can use the shared event/document context alongside run memory.
- Added regressions proving owner-scoped context reaches both model paths. Connector ingestion remains explicit; continuous polling is not claimed.

## 0.112.0

- Added the Phase A context foundation: durable owner-scoped source/event ledger with provenance, content-hash deduplication, per-source cursors and explicit visibility permissions.
- Added canonical context records and an assembler combining ranked full-text matches with recent cross-source records.
- Added a connector protocol and working signed-webhook, RSS/Atom and ICS normalizers with timestamp/signature validation and live-ready HTTP seams.
- Verification: 308 core, 133 SDK, PostgreSQL 8 passed/2 live skipped; Ruff, wheel/package/release audits clean. Cross-source injection into companion/agent arrives in the next reviewable drop; this release does not claim OAuth connectors or continuous polling.

## 0.111.0

- Fixed PostgreSQL expired-lease reaping by explicitly casting both `CASE` branches to the `meemee_job_status` enum.
- Added a regression guard rejecting implicit text branches in enum-column `CASE` assignments.
- Source verification: 303 core, 133 SDK, PostgreSQL suite 8 passed/2 live skipped; Ruff, wheel/package/release audits clean. Live 0.110.0 gate had verified every JSONB fix and exposed this final enum mismatch; final live 0.111.0 rerun is owned separately.

## 0.110.0

- Fixed live PostgreSQL JSONB writes by wrapping memory metadata, job events/results and audit metadata with psycopg's explicit `Jsonb` adapter.
- Added a regression guard covering every runtime JSONB write site that failed the live Neon gate.
- Source verification: 303 core, 133 SDK, PostgreSQL suite 7 passed/2 live skipped; Ruff, wheel/package/release audits clean. Corrected PostgreSQL evidence terminology: the local `tests_pg` suite contains 9 tests total; 7 pass locally and 2 require a live database. `test_contract.py` itself contains 4 tests at this version.

## 0.109.0

- Added PostgreSQL migration 003 for multi-instance customer identity: accounts, token ownership/kinds, email verification and password reset state.
- Added a transactional PostgreSQL account store with atomic unique signup, row-locked login failure/lockout updates, shared sessions and recovery challenges, and account-wide token revocation on disable.
- Source verification: 303 core tests, 133 SDK tests and 6 PostgreSQL contract tests passed; 15 live SDK and 2 live PostgreSQL tests skipped. Ruff, wheel package audit and explicit 0.109.0 release audit clean. Live Neon evidence is owned by the separate live-gate lane and is not claimed by this source drop until reported.

## 0.108.0

- Added self-serve customer data export for built-in identity and complete companion profile, facts, conversations, messages and check-ins.
- Added self-serve account deletion: companion content is transactionally removed, the account is disabled, all active sessions/API keys are revoked, and the tamper-evident deletion audit is retained.
- Verification: 303 core tests, 133 SDK tests and 5 PostgreSQL contract tests passed; 15 live SDK and 2 live PostgreSQL tests skipped. Ruff, wheel package audit and explicit 0.108.0 release audit clean.

## 0.107.0

- Added privacy-preserving password recovery with a uniform forgot-password response, 20-minute one-time digest-only reset links delivered through Resend, password replacement with fresh salts, lockout clearing and immediate revocation of all active account tokens.
- Verification: 302 core tests, 133 SDK tests and 5 PostgreSQL contract tests passed; 15 live SDK and 2 live PostgreSQL tests skipped. Ruff, wheel package audit and explicit 0.107.0 release audit clean.

## 0.106.0

- Added one-time, 30-minute email verification with digest-only token persistence, atomic consumption, verified account status and a public verification endpoint.
- Added Resend transactional delivery with a branded verification link, HTTPS public-URL requirement and fail-closed configuration. Signup reports whether verification mail was sent.
- Added account lockout after five consecutive login failures with a 15-minute cooldown, plus a release rehearsal for auth, email, load and backup/restore.
- Verification: 300 core tests, 133 SDK tests and 5 PostgreSQL contract tests passed; 15 live SDK and 2 live PostgreSQL tests skipped. Ruff, wheel package audit and explicit 0.106.0 release audit clean. Live Resend shared-domain send/receipt passed: provider send ID `01a0c8b7-b764-735f-849a-e73d5bfaf2a9`; Gmail INBOX message `1a0c8b7ba3f53eda` arrived about 15 seconds later with the body intact. The shared domain is limited to the provider account owner; a verified domain remains required before public delivery.

## 0.105.0

- Added a packaged public product site at `/product/` with clear landing content, current plan limits, direct signup entry, customer quickstart docs and a capability ledger.
- Product claims are tied to shipped behavior. Pricing states that payment is not integrated; the capability page names account, billing, provider and deployment gaps.
- Verification: 296 core tests, 133 SDK tests and 5 PostgreSQL contract tests passed; 15 live SDK and 2 live PostgreSQL tests skipped because external services were unavailable. Ruff, wheel package audit and explicit 0.105.0 release audit clean.

## 0.104.0

- Added built-in email/password self-serve signup and login with normalized unique email identities, PBKDF2-HMAC-SHA256 password hashing at 600,000 iterations, random per-account salts, constant-time verification and 30-day revocable bearer sessions.
- Added owner-scoped API-key creation, safe inventory and revocation. Customer keys cannot request admin scope, session tokens are excluded from API-key inventory, and raw keys are shown only once.
- Added customer account/signup/sign-in/API-key screens to `/app/`; external OIDC remains an optional deployment path.
- Assumption: built-in signup is enabled by default for ChatGPT-style self-service; operators can disable it with `MEEMEE_SIGNUP_ENABLED=false`.
- Verification: 294 core tests, 133 SDK tests and 5 PostgreSQL contract tests passed; 15 live SDK and 2 live PostgreSQL tests skipped because external services were unavailable. Ruff, wheel package audit and explicit 0.104.0 release audit clean.

## 0.103.0

- Added a packaged customer web app at `/app/` with responsive conversation history, new chats, real companion API messaging, first-run profile onboarding, timezone selection and persona editing.
- The customer surface is separate from the operator console and uses safe DOM rendering, session/bearer authentication and accessible empty/error states.
- Verification: 291 core tests, 133 SDK tests and 5 PostgreSQL contract tests passed; 15 live SDK and 2 live PostgreSQL tests skipped because their external services were unavailable. Ruff, wheel package audit and explicit 0.103.0 release audit clean.

## 0.102.0

- Added the operator console companion view: user creation and selection, persona editing, durable fact search/add/retire with provenance, a working local-channel chat box with history, and check-in settings with on-demand planning and recent-delivery status.
- Companion scopes are mintable in the console tokens view and probed in the session capability chip.
- Verification: 289 core and 148 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, deprecation-free core/SDK wheel builds, package audit and explicit 0.102.0 release audit clean. Live PostgreSQL and live WhatsApp/iMessage provider delivery remain unrun here.

## 0.101.0

- Added live booted-server SDK integration coverage for the full companion API: profiles, persona, facts, chat turns with durable fact extraction and history, check-in planning, the admin delivery tick and scope enforcement. The server under test is real end to end; only the external model endpoint is stubbed locally.
- Verification: 287 core and 148 SDK tests (15 live) plus 3 PostgreSQL contract tests passed; Ruff, deprecation-free core/SDK wheel builds, package audit and explicit 0.101.0 release audit clean. Live PostgreSQL and live WhatsApp/iMessage provider delivery remain unrun here.

## 0.100.0

- Added the companion layer: persistent per-user profiles with validated persona configuration and real IANA timezones.
- Added durable per-user fact memory with provenance, confidence, credential scrubbing, supersession and full-text retrieval.
- Added per-channel conversation persistence and a persona-conditioned conversational engine with bounded automatic fact extraction; free-text model chat shares the agent loop's bounded retry policy.
- Added proactive check-ins: idempotent slot planning from cadence and timezone-aware quiet hours on a durable queue with atomic claims, bounded retries and cancel-on-disable.
- Added channel adapters: a working local channel, a signed HTTPS webhook channel with SSRF defenses, and config-gated WhatsApp/iMessage provider adapters that fail closed with explicit configuration errors until real provider credentials exist.
- Added the scoped companion HTTP API (`companion:read`/`companion:write`) and the `meemee companion` CLI with an interactive chat and a check-in worker.
- Added the typed SDK companion resource covering profiles, persona, facts, chat, conversations and check-ins, with the new scopes in client-side validation.
- Verification: 287 core and 144 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, deprecation-free core/SDK wheel builds, package audit and explicit 0.100.0 release audit clean. Live PostgreSQL and live WhatsApp/iMessage provider delivery remain unrun here.

## 0.99.0

- Typed SDK job quota snapshots and added readiness failing-component inspection.
- Idempotency keys validate locally; keyed POSTs retry transient failures but never quota-exceeded 429 responses.
- Verification: 225 core and 136 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, deprecation-free core/SDK wheel builds, package audit and explicit 0.99.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.98.0

- Fixed SDK parity for CreatedJob quota snapshots, real quota status responses and readiness 503 diagnostics.
- Keyed job POSTs now safely retry transient transport/server failures under the bounded retry policy.
- Verification: 225 core and 118 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, deprecation-free core/SDK wheel builds, package audit and explicit 0.98.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.97.0

- Exported the previously omitted public SDK `TokenMetadata` model.
- Added mechanical package-root coverage for every public SDK model and error, and removed stale no-idempotency prose.
- Verification: 225 core and 115 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, deprecation-free core/SDK wheel builds, package audit and explicit 0.97.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.96.0

- Exported the missing SDK `IdempotencyConflictError` and mapped idempotency-specific HTTP 409 responses to it.
- Added typed queued-job idempotency-key support and regression coverage for the public import/header behavior.
- Verification: 225 core and 114 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, deprecation-free core/SDK wheel builds, package audit and explicit 0.96.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.95.0

- Synchronized all SDK public/live-integration contract labels to the core version.
- Release audit now rejects stale embedded SDK server-version labels.
- Verification: 225 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, deprecation-free core/SDK wheel builds, package audit and explicit 0.95.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.94.0

- Added the durable webhook dispatcher to the safe default Kubernetes pod so queued events are delivered.
- Webhook-worker now uses the configured payload ceiling as well as the required encryption key.
- Verification: 224 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, deprecation-free core/SDK wheel builds, package audit and explicit 0.94.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.93.0

- Fixed durable worker startup by passing the required vault key to its webhook outbox.
- Worker and API now use the same configured webhook payload ceiling and encryption settings.
- Verification: 223 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, deprecation-free core/SDK wheel builds, package audit and explicit 0.93.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.92.0

- Replaced the unsafe split-pod SQLite Kubernetes topology with one API/worker pod on one host.
- Added Recreate rollout, read-only root filesystems and distinct dependency readiness/liveness probes.
- Verification: 222 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, deprecation-free core/SDK wheel builds, package audit and explicit 0.92.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.91.0

- Fixed the commercial container build context to include console, PostgreSQL assets and LICENSE while excluding secrets and local state.
- Updated Kubernetes image pins and changed its API readiness probe from liveness `/health` to dependency-aware `/ready`.
- Verification: 222 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, deprecation-free core/SDK wheel builds, package audit and explicit 0.91.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.90.0

- Added a least-privilege GitHub commercial release gate for every push and pull request.
- CI runs lint, all local suites, PostgreSQL contracts, wheel builds and both release audits, then uploads verified wheels.
- Verification: 220 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, deprecation-free core/SDK wheel builds, package audit and explicit 0.90.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.89.0

- Package audit now verifies complete wheel RECORD coverage, SHA-256 digests and byte sizes.
- Added post-build payload tampering regression coverage.
- Verification: 219 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, deprecation-free core/SDK wheel builds, RECORD-verifying package audit and explicit 0.89.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.88.0

- Hardened package audit against malformed/corrupt archives, path traversal and duplicate ZIP members.
- Release validation now requires the correct distribution identity and wheel RECORD metadata.
- Verification: 218 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, deprecation-free core/SDK wheel builds, hardened package audit and explicit 0.88.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.87.0

- Package audit now requires the proprietary license expression and one valid bundled license file.
- Added corruption regressions so missing commercial/legal metadata cannot pass the release gate.
- Verification: 217 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, deprecation-free core/SDK wheel builds, package audit and explicit 0.87.0 release audit clean. Live PostgreSQL remains unrun here.

## 0.86.0

- Modernized core and SDK proprietary license metadata to the current packaging standard.
- Both real wheel regressions now require bundled license files and reject the deprecated metadata warning.
- Verification: 216 core and 112 SDK tests plus 3 PostgreSQL contract tests passed; Ruff, deprecation-free core/SDK wheel builds, package audit and explicit 0.86.0 release audit clean. Live PostgreSQL remains unrun here.

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
