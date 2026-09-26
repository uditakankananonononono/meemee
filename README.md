# Meemee

Meemee is Udita's private, local-first agent runtime. It turns a goal into an inspectable plan, gives a model a bounded set of real tools, records every result, and stops honestly when it finishes or cannot continue. This is a working commercial-grade single-host runtime, not a claim to be finished general intelligence. Read [STATUS.md](STATUS.md) for the exact verified, thin and missing ledger, and [CHANGELOG.md](CHANGELOG.md) for release history.

## Verified in v0.122.0 (186)

Items 164-166, 172 and 174 are unreleased pb7 work, items 167-171 are unreleased pb4 work, items 173 and 175 are unreleased main-lane work item 176 is unreleased model-layer work item 177 is unreleased pg-email-verification work item 178 is unreleased pg-quotas-entitlements work item 179 is unreleased pg-runs work item 180 is unreleased pg-webhooks work item 181 is unreleased pg-companion work item 182 is unreleased pg-personal-context work item 183 is unreleased pg-monitors-reflection work item 184 is unreleased pg-deletion-browser work and item 185 is unreleased pg-vault-rotation work, all verified in this tree; the rest shipped in v0.122.0.

1. Strict JSON agent loop with a configurable step limit.
2. OpenAI-compatible model client for Ollama, vLLM, llama.cpp, or hosted endpoints.
3. Typed tool registry with JSON Schema and validated arguments.
4. Read/write/execute risk classes and a write approval gate.
5. Live GitHub repository search with filters and quality ranking based on popularity, recency, maintenance, and archive status.
6. Workspace-confined file read/write tools with traversal protection.
7. Durable SQLite event memory with WAL, FTS5 retrieval, and run provenance.
8. CLI for runs, GitHub scouting, API serving, queued jobs and workers.
9. FastAPI health/run endpoints, Docker image, and automated tests.
10. Allowlisted direct-argv command execution with no shell interpolation, timeout, output cap, and explicit approval.
11. Git status/diff/log inspection plus approval-gated local commits of explicit paths; no implicit push.
12. Durable scheduled job queue with atomic multi-process claims, retry limits, and result/error records.
13. Long-running worker command and job create/status API.
14. Optional constant-time bearer-token authentication for run and job endpoints.
15. Concurrent sub-agent delegation with isolated agent instances, bounded concurrency, and ordered result provenance.
16. AES-256-GCM encrypted local secrets vault with per-record nonces and no secret-value listing.
17. Playwright Chromium navigation with title/text/status/screenshot capture, public-address enforcement, and workspace-safe screenshot paths.
18. Kubernetes API/worker deployments, persistent data claim, probes, resource limits, and restricted container security contexts.
19. Browser click/fill/press/select/wait action sequences with optional persistent named profiles and action provenance.
20. Queued-job cancellation with explicit state conflict handling.
21. Sliding-window API rate limiting with Retry-After responses.
22. Append-only queued-job event logs with cursor-based retrieval for progress clients.
23. Versioned persistent plan DAGs with cycle validation, arbitrary dependency order, status transitions, full history, change reasons, and optimistic concurrency control.
24. Persistent revocable API tokens with SHA-256 digest storage, expiry, last-used audit, one-time reveal and endpoint scopes.
25. Production API request IDs, safe 500 responses, security headers, readiness checks, exact package version reporting, and validated scheduling input.
26. Federated OIDC bearer authentication with signed JWKS validation, strict issuer/audience/expiry requirements, safe algorithm allowlist, configurable role-to-scope mapping, and direct OAuth scope support.
27. Append-only tamper-evident SHA-256 audit chain for runs, jobs and token administration, with chain verification and cursor-based admin retrieval.
28. Forward-only transactional schema migrations with contiguous versions, idempotency and checksum drift detection.
29. Online consistent SQLite backups with per-file SHA-256 manifests, integrity checks, restore verification and tamper detection.
30. Prometheus metrics for request volume, route/status, latency, in-flight work, agent outcomes and queued jobs, behind admin authorization.
31. Configurable production JSON logging with UTC timestamps, correlation fields and exception rendering.
32. Interactive OIDC Authorization Code login with PKCE S256, signed expiring state/session cookies, CSRF state verification, secure cookie flags, scoped session authorization and logout.
33. Atomic SQLite-backed rate limits shared across single-host API processes, isolated by credential digest or client IP, with health exemptions, automatic cleanup and standard limit/remaining/reset/retry headers.
34. Resume-safe Server-Sent Events for durable job progress, with Last-Event-ID cursors, ordered replay, terminal close, heartbeats and proxy anti-buffering headers.
35. Cooperative running-job cancellation persisted through requested/cancelled states, observed cross-process by workers, checked between agent steps and emitted as durable events.
36. Production model transport with connection pooling, separate connect/request timeouts, bounded retries for transient network/408/429/5xx failures, Retry-After support, jittered exponential backoff, fail-fast permanent errors and strict payload validation.
37. Deterministic deny-first tool policy with allow/deny lists, risk-based approvals, path/host patterns, argument-size limits and file-based operator configuration.
38. Principal-scoped durable idempotency for job creation, with request hashing, exact replay, conflict rejection, route isolation, bounded keys and 24-hour expiry cleanup.
39. Bounded-latency cached readiness checks across memory/jobs/auth databases, model endpoint and free disk, with per-component diagnosis, safe errors and proper HTTP 503 failure signaling.
40. Atomic per-principal daily job quotas with configurable defaults, admin overrides, UTC resets, status endpoint, rollback on excess, audit events and quota details returned at job creation.
41. Configurable data-retention execution for terminal jobs/events, memories, expired idempotency and old rate windows, with active-job protection and deletion reporting; tamper-evident audit is deliberately retained intact.
42. Production Python client SDK with sync/async clients, typed models, retries, idempotency and resumable SSE, tested against a live booted server.
43. Static operator web console for health, jobs, SSE progress, audit verification and token/quota operations, served at `/console/`.
44. Additive PostgreSQL persistence package for memory, jobs, plans, tokens, audit and migrations, with pooled connections, leased/fenced job claims, checksummed migrations, contract tests and optional live-server integration tests. Core runtime selection is not wired yet.
45. Secret-safe tool provenance: recursive redaction by sensitive field name, stable SHA-256 fingerprints for correlation, and bounded large-text recording with size/hash/preview metadata.
46. Inbound credential scrubbing before planning, model prompts, run reports or memory persistence, covering common GitHub/OpenAI/Bearer formats and named secret assignments with correlation-safe markers.
47. Bounded graceful API shutdown: stop accepting new immediate runs, wait for active runs, report grace timeout, then close pooled model transport resources.
48. Exact-tool approvals: per-run allowlists plus persistent per-principal grants with expiry, revocation, admin APIs, unknown-tool rejection and audit events.
49. Durable signed webhook outbox with principal subscriptions, event filters/wildcards, event deduplication, SSRF-safe HTTPS endpoints, HMAC-SHA256 delivery signatures, bounded retries and terminal failure state.
50. Running-service webhook integration: authenticated create/list/delete APIs with one-time secrets, dispatcher-worker CLI, and idempotent job done/failed/cancelled event emission from workers and queued cancellation.
51. Webhook operations: principal-scoped delivery list/status, owner-safe failed replay with audit, outbox status Prometheus gauges, and terminal-record retention cleanup that never deletes queued/sending work.
52. Webhook hardening: stale dispatcher-lease recovery, owner-scoped one-time secret rotation, and audited test-delivery enqueue for end-to-end endpoint validation.
53. Webhook health control: owner pause/resume, per-subscription status summaries, per-delivery attempt timelines, and a circuit breaker that suspends subscriptions after five terminal delivery failures.
54. Webhook recovery operations: configurable circuit-breaker cooldown before resume, terminal success-rate/suspended/oldest-queue Prometheus gauges, and documented production alert thresholds and recovery sequence.
55. Webhook payload contract: `meemee.webhook.v1` envelope, configurable byte ceiling, per-subscription top-level data-field selection, and persisted SHA-256 body hash exposed without the body for receiver reconciliation.
56. Receiver compatibility: HMAC/timestamp verification helper and vault-backed CLI, custom non-secret subscription headers with reserved/sensitive-name bans and size limits, plus a runnable verified FastAPI receiver fixture.
57. Encrypted webhook signing secrets at rest with AES-256-GCM, per-record random nonces, subscription-bound authenticated context, automatic transactional plaintext upgrade, encrypted rotation, and fail-closed missing/wrong-key startup.
58. Production preflight CLI with machine-readable pass/fail output for bootstrap-token strength, vault-key validity, data-directory writability/permissions/free space, SQLite quick integrity checks, model reachability and the supported single-host boundary.
59. Secure first-run `meemee init` onboarding that creates owner-only data/config paths, generates strong bootstrap and vault credentials, uses exclusive atomic configuration creation, refuses overwrite/non-empty targets, redacts generated secrets from output, and prints exact preflight/start steps.
60. Deterministic commercial release audit CLI that fails on missing required assets, version-source/document drift, and explicit stub markers across shipped text/code, with structured findings suitable for CI and a complete proprietary license file.
61. Distribution packaging verification: wheel now includes the full operator console and PEP 561 type marker; `meemee package-audit` validates version metadata, CLI entry point and required wheel contents; release evidence includes isolated wheel installation, CLI help/init execution and installed console import.
62. HTTP security baseline across API and packaged console: configurable trusted-host rejection, restrictive CSP, frame/content/referrer/permissions controls, no-store for API/auth responses, and operator-enabled HSTS with configurable max age.
63. OpenAPI authentication correctness: protected operations now declare a standard HTTP Bearer security scheme instead of exposing Authorization as an ordinary optional header, with current-version and commercial-endpoint schema regression coverage.
64. Commercial Python SDK parity for quota get/set, exact-tool approval list/grant/revoke, and webhook create/list/delete/rotate/delivery/replay operations, with typed models and live-server regression updated for required encrypted-secret startup.
65. Enforceable commercial packaging metadata with persistent starter/team/business plan assignments, plan-derived daily-job and active-webhook limits, authenticated entitlement APIs, public machine-readable product catalog, audited admin assignment, and explicit external-billing/no-price boundary.
66. Complete plan-limit enforcement and usage visibility: active non-expired persistent approvals are counted and capped per plan without blocking regrant of an existing tool, while the current entitlement response reports exact daily-job, active-webhook and persistent-approval usage beside limits.
67. Exact authenticated account introspection through `/v1/whoami` with principal ID/name, sorted scopes, plan, limits and live usage; the packaged operator console renders this account/plan state and removes its former whoami gap while retaining probes only as compatibility fallback.
68. Principal-owned queued jobs with additive legacy-safe owner migration, owner-scoped get/cancel/events/SSE reads, status/cursor/limit list API, Python SDK listing, and source-of-truth console listing that no longer depends on browser-local job history.
69. Safe API-token inventory with admin-only cursor/filter listing of ID, name, scopes and lifecycle timestamps while never selecting or returning token digests or raw credentials; SDK and console use the server inventory.
70. Principal-owned completed run history with durable reports, owner-scoped cursor list/get APIs, Python SDK support and source-of-truth console history, eliminating the remaining browser-local product histories.
71. Commercial-state operations parity: complete SQLite online backup automatically captures run, entitlement, approval and webhook databases with verified manifests; readiness now checks run/entitlement stores; configurable run-history retention deletes old reports and reports exact counts.
72. Operator encryption-key rotation for vault records and encrypted webhook signing secrets, with hidden confirmation input, full old-key validation before writes, per-database verified safety backups, interruption marker/startup refusal, rollback restoration on failure, and post-rotation old-key rejection.
73. Account portability CLI for owner-scoped jobs, completed runs and entitlement assignment: deterministic checksummed export, overwrite refusal, checksum/dry-run inspection, target-principal remapping, complete collision preflight, rollback safety copies and transactional per-database import.
74. Checksummed component schema registry for evolving jobs, webhooks, runs and entitlements stores with version visibility, idempotent registration, same-version drift refusal, newer-database startup refusal and cross-database `schema-status` operator report.
75. Opaque stable keyset pagination for principal jobs, completed runs and safe token metadata, with tamper/malformed cursor rejection, backward-compatible legacy before filters, explicit next_cursor envelopes, and Python SDK page/iter_all helpers.
76. Pagination consistency for webhook subscriptions, webhook delivery history and the audit chain: stable keyset/sequence continuation, next_cursor envelopes, malformed-cursor refusal and Python SDK page/iterator support while retaining legacy delivery/audit cursors.
77. Release-blocking shared-store contention harness spanning token authentication, tamper-evident audit appends, owner-bound job writes and entitlement reads, with configurable concurrency, exact lane/write counts, error capture, audit-chain verification and nonzero CLI exit on any mismatch.
78. Full-ASGI API parallel load gate that drives health, authenticated whoami and owner job-list requests through trusted-host, metrics, rate-limit, auth, request-context and routing middleware, requiring complete responses, request IDs and zero unexpected 5xx/network errors.
79. Externally storable HMAC-SHA256 audit-chain head checkpoints with no-overwrite creation, pre-anchor chain validation, historical-head verification and tamper/wrong-key detection.
80. Bounded model-driven replanning after evidence invalidates the active plan, with strict structured requests, validated replacement DAGs, a three-revision ceiling, model feedback and durable run-memory provenance.
81. Signed-checkpoint-gated audit prefix pruning that refuses invalid or stale checkpoints, retains a cryptographic chain base, verifies the surviving suffix and keeps historical anchors verifiable.
82. Local deterministic semantic memory embeddings with durable vectors, cosine reranking and reciprocal-rank fusion with FTS, automatically used for agent context without a network dependency.
83. Approval-gated GitHub branch push and pull-request tools with token-required API mutations, exact commit targeting, optimistic remote-head protection, structured errors and canonical result URLs.
84. Browser per-run domain policy, workspace-confined managed downloads, and explicit CAPTCHA/security-challenge detection that returns a human-required handoff signal without attempting bypass.
85. Argument-scoped persistent tool approvals with exact constraint-subset matching, API grant/list visibility, runtime enforcement and safe regrant/revoke behavior, enabling repository- or command-specific permissions.
86. Operator-console permissions administration with principal grant registry, constrained/unconstrained grant forms, expiry controls, explicit confirmation and revocation, backed by the scoped approval APIs.
87. Authenticated owner-scoped WebSocket job-event streaming with durable cursor replay, token/OIDC/session authentication, scope enforcement, terminal closure and explicit 44xx rejection codes.
88. Cooperative mid-tool cancellation for asynchronous tools with prompt task cancellation, cleanup propagation, a recorded cancelled tool result and immediate agent termination before another model step.
89. Fail-closed persistence composition selector with explicit SQLite/PostgreSQL configuration, automatic PostgreSQL migrations, optional dependency packaging and runtime agent-memory selection; queued-job/API parity remains an explicit boundary.
90. Python SDK parity for argument-scoped approval administration, including corrected current response models, validated constraint maps, expiry support and safely encoded principal/tool paths.
91. Core/SDK version parity with stale server-contract labels removed across package metadata, imports, README and live-integration documentation.
92. Checksummed SQLite-to-PostgreSQL core-data export and transactional empty-target import CLIs covering memory, jobs/events, tokens and audit, with tamper detection, byte preservation and fail-closed collision checks.
93. API/worker composition selection for PostgreSQL memory and owner-scoped queued jobs, with automatic ownership migration, owner reads/lists/events and leased worker execution.
94. PostgreSQL job-list opaque keyset cursor parity with malformed-cursor rejection, stable updated-at/UUID ordering and compatible next-cursor envelopes.
95. Backend-neutral persistence lifecycle with one shared composition per API/worker process, SQLite/PostgreSQL readiness probes, shared worker memory and clean pooled-database shutdown.
96. PostgreSQL production preflight that fails on missing DSNs, connection/pool errors or unapplied migrations and reports the exact selected deployment boundary.
97. PostgreSQL-backed atomic fixed-window rate limiting shared across hosts/pods, selected automatically with the PostgreSQL backend and retaining compatible headers, cleanup and token/IP identity semantics.
98. PostgreSQL agent-memory contract parity for secret-scrubbed writes and hybrid/semantic retrieval calls, using ranked native full-text fallback until an optional vector extension is configured.
99. Fail-closed readiness disk probing that handles not-yet-created data directories and disk-stat failures without crashing the health endpoint.
100. Cancellation propagation through delegated agent teams, preventing new child work after cancellation and passing the shared signal into active child agents and async tools.
101. PostgreSQL runtime package and bundled SQL migrations included in the commercial wheel, enforced by package audit and a real wheel-build regression test.
102. Typed Python SDK wheel build verification with core-version parity, required client/model modules and PEP 561 `py.typed` marker inspection.
103. Operator-console account administration for IdP-backed principals, including explicit plan assignment and validated daily-job quota overrides with confirmation and structured results.
104. Release-audit regression guards for core/SDK version drift and stale capability claims, preventing shipped features from remaining falsely listed as missing.
105. Current deployment assets and operator guidance for SQLite/PostgreSQL boundaries, distributed rate limiting, checksummed migrations and all required persistence/audit environment controls.
106. Release-ledger numerical integrity checks plus current PostgreSQL evidence and signed-audit-retention operations, removing stale counts and obsolete operator claims.
107. Console and operator documentation synchronized to the current API, stream, account-administration and deployment image surfaces, guarded against legacy v0.46 claims.
108. Verified backup restore rehearsal CLI with source integrity checks, empty-destination enforcement, SQLite online restore, post-restore integrity/checksum validation and partial-failure cleanup.
109. Operator console job inventory uses the implemented owner-scoped server listing, with regression protection against the obsolete browser-only/no-list-endpoint claim.
110. Standards-compliant proprietary package metadata and license-file inclusion in both core and SDK wheels, guarded by real build regressions without setuptools deprecation warnings.
111. Package audit enforces the core wheel commercial license expression and validates the single bundled proprietary license text, preventing incomplete legal metadata from passing release gates.
112. Supply-chain wheel validation rejects malformed archives, CRC corruption, unsafe paths, duplicate members, wrong distribution identity and missing RECORD metadata before release.
113. Wheel RECORD attestation verifies complete member coverage plus every SHA-256 digest and byte size, rejecting post-build payload tampering before release.
114. Least-privilege GitHub commercial release gate runs lint, core/SDK/PostgreSQL-contract suites, both wheel builds, tree/wheel audits and uploads verified distribution artifacts on every push and pull request.
115. Commercial container context includes console, PostgreSQL runtime/migrations and proprietary license, excludes local secrets/state, probes readiness rather than liveness, and keeps Kubernetes image pins current.
116. Safe default Kubernetes topology co-locates one API and one worker in a single Recreate pod for SQLite state, with read-only roots, separate liveness/readiness probes and no unsafe cross-node PVC writers.
117. Durable job worker now supplies the required vault encryption key and configured payload ceiling to its webhook outbox, eliminating a startup failure and keeping API/worker webhook configuration identical.
118. Safe default deployment includes the durable webhook-dispatcher sidecar, with the same encrypted outbox and payload configuration, so queued signed deliveries actually leave the system.
119. SDK package metadata and all public/live-integration contract prose are synchronized to the core version, with a release regression rejecting stale embedded server-version labels.
120. SDK queued-job idempotency exposes the server header, maps payload/key conflicts to an exported typed `IdempotencyConflictError`, and preserves generic conflict behavior for other HTTP 409 responses.
121. SDK root exports are mechanically checked against every public typed model and error, closing missing-import failures such as `TokenMetadata` and preventing CI-only export drift.
122. SDK contract parity preserves job quota snapshots, models real day-based quota responses, exposes readiness as a boolean while parsing 503 diagnostics, and safely retries transient keyed job POSTs.
123. SDK job quota snapshots are typed, readiness identifies failing components, idempotency keys are locally bounded to 1-200 characters, and quota-exceeded 429 responses never retry even when keyed.
124. Persistent per-user companion profiles bind a validated persona (name, tone, style rules, language, emoji and custom instructions) and a real IANA timezone to a durable identity.
125. Durable per-user fact memory records category, confidence and provenance, scrubs pasted credentials, deduplicates exact repeats, supports full-text retrieval, and retires facts through supersession without deleting history.
126. Per-user, per-channel conversation persistence replays bounded history into every model prompt and rejects cross-user conversation access.
127. The conversational engine conditions free-text replies on persona and recalled facts, then extracts bounded durable facts from each exchange with conversation provenance, skipping malformed extraction payloads safely.
128. Free-text model chat uses the same bounded retry policy as the agent decision loop, retries only transient failures, and rejects empty completions.
129. Proactive check-ins plan idempotent per-user slots from cadence and timezone-aware quiet hours, including overnight windows, in a durable queue with atomic claims, bounded retries and cancel-on-disable.
130. Check-in delivery generates a persona-conditioned message and delivers it through the channel registry, recording per-check-in message, status and error evidence.
131. Channel adapters include a working local channel persisted to the user's conversation and a signed HTTPS webhook channel with SSRF defenses, HMAC signatures and payload ceilings.
132. WhatsApp and iMessage provider adapters are real HTTP deliveries that stay config-gated: without provider endpoint and token they fail closed with an explicit configuration error naming the missing settings.
133. The companion HTTP API (profiles, persona, facts, chat, conversations, check-in planning and an admin delivery tick) and the `meemee companion` CLI (user/persona/fact management, interactive chat, check-in worker) enforce dedicated companion:read and companion:write scopes with audit events.
134. The typed SDK companion resource covers users, persona and check-in updates, fact CRUD and search, chat turns, conversations and message history, check-in planning and the admin delivery tick, with the new scopes included in client-side scope validation.
135. Live booted-server integration tests exercise the companion API end to end through the SDK: profiles and persona, fact CRUD and search, chat turns with durable fact extraction and history, check-in planning, the admin tick and scope enforcement, against a stubbed external model endpoint.
136. The operator console ships a companion view: user creation and selection, full persona editing, durable fact search/add/retire with provenance, a working local-channel chat box with history, and check-in settings with on-demand planning and recent-delivery status; companion scopes are mintable in the tokens view and probed in the session chip.

137. Packaged customer-facing web app at `/app/` with profile onboarding, responsive conversation history, companion chat and persona editing, separate from the operator console.

138. Built-in email/password self-serve signup and login with salted PBKDF2 password storage, revocable expiring sessions and optional external OIDC.
139. Customer-owned API-key creation, safe inventory and revocation with bounded non-admin scopes and one-time raw key reveal.

140. Packaged public product site at `/product/` with landing content, enforced plan presentation, customer quickstart docs and an honest current capability/gap ledger.

141. Five-failure account lockout with a 15-minute cooldown and successful-login reset, layered on the existing global rate limiter.
142. One-time email verification with expiring digest-only tokens, account status, public confirmation endpoint and Resend transactional delivery behind fail-closed HTTPS configuration.

143. Password recovery with uniform account-enumeration-safe responses, 20-minute digest-only one-time links, fresh salted password storage and immediate active-token revocation.

144. Self-serve customer data export and account deletion with transactional companion-content removal, account disablement, full token revocation and retained tamper-evident deletion audit.

145. PostgreSQL multi-instance customer identity schema and transactional store for accounts, owned sessions/API keys, row-locked login/lockout updates and recovery challenges.

146. Explicit psycopg JSONB adaptation for PostgreSQL memory metadata, job event/result payloads and audit metadata, guarded across all runtime write sites.

147. Explicit PostgreSQL enum casts for both expired-lease reaper status branches, removing the final live type mismatch from the Neon gate.

148. Owner-scoped unified source/event ledger with provenance, cursoring, content dedupe, visibility permissions, ranked context assembly and working signed-webhook/RSS/Atom/ICS connector normalization.

149. Cross-source unified personal-context retrieval and provenance grounding wired into both companion chat and autonomous agent runs.

150. Verified-address Resend task-email bridge plus a Gmail read-only reply connector with explicit OAuth status, durable cursor and provenance normalization.

151. Evidence-backed typed personal model with owner isolation, conflicts/supersession, confidence, temporal validity, user deletion and model-path grounding.

152. Bounded source-grounded personal reflection with strict evidence citation validation, unsupported-claim rejection and temporal expiry.

153. Personal-model v1 closeout: user correction provenance, owner-scoped evidence inspection and safe confidence decay that never weakens explicit corrections.
154. Named model profiles with per-role routing (agent, chat, reflection), ordered fallback with a per-attempt record, and free-first gating: local Ollama by default, paid hosted profiles (Sakana Fugu) only with a key plus MEEMEE_ALLOW_PAID_MODELS=true. See [docs/models.md](docs/models.md).
155. Inkling through the Hugging Face Inference Providers router (`inkling` = Inkling-Small, `inkling-large` = Inkling) with a free HF token, falling back to local on rejection or exhausted credits; zero-token `/models` health probe; `meemee models list|check`.
156. Self-hosted Inkling-Small: hardware-floor detection (NVIDIA VRAM, RAM, disk), best-plan selection across vLLM NVFP4/BF16 and llama.cpp Unsloth GGUF, exact launch/download commands, `meemee models inkling-local [--run]` and `deploy/inkling/serve-inkling.sh`. Never substitutes a smaller model. See [deploy/inkling/README.md](deploy/inkling/README.md).
157. Per-turn model audit trail: every companion reply returns and durably stores which profile and model answered, with the full fallback attempt list; traces are task-isolated under concurrent requests and included in customer export and deletion.
158. Scheduled personal-model reflection (`meemee reflection-worker`) on the reflection model route: reflects only owners with new context evidence after `MEEMEE_REFLECTION_INTERVAL_MINUTES` (default 360), records status and model trace per owner, never advances past failed runs, and writes audit events.
159. `GET /v1/models/status` (scope `jobs:read`) for the web app: every model profile with availability and reason, active routes and paid-model gating, never exposing keys; `?probe=true` checks each routed profile's `/models` endpoint concurrently (3s timeout, zero tokens) and reports which profile is serving each role.

160. Interactive browser human takeover: live browser sessions stay open across agent steps; a detected challenge (captcha, "verify you are human", MFA prompt) or an agent/operator request creates a one-time, expiring takeover link; the person gets a live view at `/browser/takeover` streamed over WebSocket and their clicks, drags (slider challenges), typing, allowed keys, scrolls and in-policy navigation drive the same page; handing back returns control to the agent on the same page and cookies. Tokens are stored as SHA-256 digests, typed text is logged by length only, domain policy applies to human navigation, sessions lost to a restart are marked `lost`. With `notify_user_id` the link is queued durably and delivered on the user's companion channel (local conversation by default), then erased from the queue. Tested against real Chromium, including an end-to-end run through a booted server. See [docs/browser-takeover.md](docs/browser-takeover.md).

161. Product-wide account deletion: `DELETE /v1/account`, admin `DELETE /v1/admin/principals/{id}/data` and `meemee account-delete PRINCIPAL --yes` hard-delete the principal's jobs and job events, run reports, agent memories (with embeddings and full-text rows), personal model including soft-deleted history, connected-context sources and records, monitors, webhook subscriptions/deliveries/attempts, quotas, plan assignment, idempotency records, standing tool approvals and companion content. Running jobs are tombstoned (goal/owner wiped, cancellation requested) and deleted when the worker settles, including memories written after the purge; synchronous runs that finish after deletion are discarded with 410. Every step is recorded in a durable deletion ledger and resumed after a crash (`meemee account-delete-resume`, also on API start). SQLite and PostgreSQL job backends are covered; the PostgreSQL path passed a live run. The tamper-evident audit log is retained by design.

162. Forced interruption of blocking tools: a tool that sets `isolation = IsolationPolicy(...)` runs in a spawned child process; cancellation or its hard timeout sends SIGTERM, then SIGKILL after a grace period, and the child is always reaped. Covered by `tests/test_isolation.py` (12 tests, including a SIGTERM-ignoring child and an event loop that stays responsive while the tool blocks).

163. Companion console screens (branch pb4): profile editor, persona editor, facts with retire, conversations browser across channels with timestamped paged history, check-in queue with status filter and admin delivery run, verified by a headless Chromium test against a live server.

164. End-to-end run path, verified live (branch pb7, unreleased): `tests/test_live_runs_e2e.py` boots the real server and worker in SQLite and PostgreSQL modes against a local OpenAI-compatible provider with a fixed script, and checks `POST /v1/runs` (model call, real `workspace.read_file` tool, tool result fed back, final answer), run storage and owner scoping, `run.create` in a verified audit chain, a queued job streamed over SSE from `queued` to `done` and replayed, routed-profile fallback, and a 502 on model outage. It found and fixed a bug where every rate-limited request returned 500 in PostgreSQL mode.

165. Write-approval gate verified live (branch pb7, unreleased): in SQLite and PostgreSQL modes, a write tool is refused without approval (nothing written, the model sees the denial), per-run `approved_tools` / `approve_writes` allow it, persistent grants honor argument constraints, principal scoping, expiry and revocation, unknown tools cannot be granted, and `approval.grant` / `approval.revoke` / `run.create` land in order in a verified audit chain. Fixed: queued jobs ignored persistent grants (the worker passed no approval callback), so a job could never use an approval-gated tool; jobs now apply the owner's grants exactly as runs do.

166. Structured refusals (branch pb7, unreleased): every run report, stored run and finished job result carries `approvals_required`, a list of refused tool calls with `step`, `tool`, `risk`, `reason` (`approval_required` or `policy_denied`), redacted `arguments`, `grantable`, and the exact `per_run` body or `persistent_grant` (`PUT /v1/approvals/{principal}`) that would have allowed the call. An empty list means nothing was refused; clients must not present a run with refusals as done, whatever the model's `final` text says. The SDK exposes `RunReport.approvals_required`, `RunReport.is_blocked`, `Job.approvals_required` and `ApprovalRefusal`. Live tests submit the suggested bodies unchanged and the retried run or job succeeds.
167. SDK asyncio client and WebSocket job streaming (branch pb4): `AsyncMeemeeClient` mirrors the full synchronous SDK surface with the same models, errors and retry policy, async SSE resume and thread-offloaded blocking auth; `jobs.stream_ws` (sync and async) follows `/v1/jobs/{id}/ws` with `?after` resume, ping/pong dead-peer detection and typed 44xx errors, verified against a scripted WebSocket server and a booted server. Fixed the WebSocket handler to accept before rejecting, so 4400/4401/4403/4404 close codes reach real network clients instead of a bare HTTP 403.

168. Token introspection (branch pb4): admin `POST /v1/tokens/introspect` takes a raw credential in the body and returns its state (active, revoked, expired or unknown), credential type (API token, bootstrap or OIDC), scopes, principal, created/last-used/expiry/revocation times and a redacted form showing only the first and last four characters; the secret and its digest are never returned or audited, unknown tokens answer `active: false` instead of 404, and introspection does not count as use. SQLite and PostgreSQL token stores share the same semantics (PostgreSQL verified live), and the SDK exposes `tokens.introspect` on both sync and async clients, verified against a booted server.

169. asyncio-native OIDC client credentials in the SDK (branch pb4): `AsyncOIDCClientCredentialsAuth` discovers the token endpoint, fetches and caches tokens on `httpx.AsyncClient` with leeway refresh and a single shared fetch for concurrent callers, and is awaited by `AsyncMeemeeClient` without a worker thread; sync-style providers keep the thread-offload fallback. Verified with mocked issuers and live against a local HTTPS issuer whose RS256 tokens the booted server validates through JWKS.

170. SDK 401 recovery (branch pb4): with a refresh-capable auth provider, sync and async clients answer a 401 with exactly one forced credential refresh and one retry of the same request (JSON bodies rebuilt, SSE and WebSocket streams reopened from scratch, reconnect and retry budgets untouched); a repeated 401 raises, and static tokens keep failing fast. Verified mocked and live, including revoked-then-valid tokens and OIDC tokens rejected after issuance.

171. SDK live runs (branch pb4): `sdk/tests/test_live_runs.py` drives the sync and async SDK clients against a booted server and `meemee worker` with a local scripted OpenAI-protocol provider, in SQLite and PostgreSQL modes: `runs.create` parses the final answer, step count and real `workspace.read_file` tool result, `runs.get/list/iter_all` read the stored run with cursor paging, other owners get 404 and a token without `runs:write` gets 403, queued jobs are streamed live to `done` over SSE and WebSocket with the result parsed, and a model outage raises `ServerError` 502 without an automatic retry. It found and fixed two PostgreSQL-mode bugs: the job WebSocket dropped the connection on the first event (UUID and datetime values were not JSON-encodable), and the SDK rejected finished jobs because the PostgreSQL store returns `result` as an object instead of a JSON string.

172. Blocked flag (branch pb7, unreleased): run reports, `GET /v1/runs[/{id}]`, `GET /v1/jobs[/{id}]` and the `job.done` webhook carry a top-level `blocked` boolean, true when `approvals_required` is non-empty. Job `status` stays `done` so existing filters and clients are unaffected; a `done` job with `blocked: true` finished its run but did not do everything asked. `run.create` audit entries record `blocked` and the refusal count. Job `result` is now a JSON string on both backends (PostgreSQL mode returned decoded JSON, which the SDK's `Job` model rejected). SDK: `RunReport.blocked`, `Job.blocked`, `Job.is_blocked` (falls back to `approvals_required` against older servers). Verified live on SQLite and PostgreSQL 16.2.

173. PostgreSQL-backed tool-approval grants (main, unreleased): in PostgreSQL mode `PUT/GET/DELETE /v1/approvals/...`, run approval checks, queued-job approval checks, `whoami` usage and account purge all use `meemee_tool_approvals` (migration 005) through `Persistence.approvals`, so grants made on one host apply to workers and API servers on other hosts; SQLite mode still uses `approvals.sqlite3`. Both stores share one contract (`ApprovalStoreInterface`), normalize `expires_at` to UTC (fixing offset timestamps that SQLite compared as text) and reject non-ISO expiries with 422. `tests/test_approvals.py` runs every grant test on both backends; `tests/test_approvals_multihost_e2e.py` boots two API servers and a worker with separate data directories on one PostgreSQL database and checks grant on A -> listed on B -> used by B's runs and the worker's jobs -> revoked on B -> refused everywhere, plus the single-host SQLite path. Existing grants move with `python -m meemee_persist_pg.cli ... --approvals approvals.sqlite3 copy`; making that copy work found and fixed two cutover bugs that affected every non-empty store (`Connection.executemany` does not exist in psycopg 3, and decoded JSON columns were not adapted to jsonb).

174. Tenant-scoped webhooks and live webhook verification (branch pb7, unreleased): SECURITY FIX - job webhook events were delivered to every principal's subscriptions; they now reach only the job owner's. Test-only `MEEMEE_WEBHOOK_ALLOW_PRIVATE_HOSTS=1` lets a local HTTPS receiver pass the SSRF guard and is refused when `MEEMEE_ENV=production` (never set it on a deployed instance). Verified live against a real receiver: signed `job.done` with `blocked`, signature scheme, retry after 500, cross-tenant isolation, with SQLite and PostgreSQL job stores.

175. PostgreSQL-backed API tokens, accounts and audit chain (main, unreleased): in PostgreSQL mode the API, CLI account deletion and the reflection worker take `tokens` and `audit` from `build_persistence`, so tokens and accounts minted, revoked, reset or disabled on one host apply on every host, and every host appends to and serves one global audit chain. The PostgreSQL `TokenStore` now has the full SQLite surface (owner-scoped tokens, session tokens that authenticate as their account, listing with cursors, owner-scoped revoke, signup, login lockout, password reset, disable), and both stores reject non-ISO token expiries (SQLite used to compare them as text, so `never` outlived every date). Audit appends run under the advisory lock at READ COMMITTED; the previous SERIALIZABLE setting failed concurrent appends with serialization errors (reproduced by the new test). Migration 006 adds a chain base so pruned SQLite chains verify after cutover; the cutover copies token owner/kind, accounts and the chain base. Verified on both backends by `tests/test_token_audit_backends.py`, across two API servers with separate data directories by `tests/test_tokens_audit_multihost_e2e.py` (token minted on A used and revoked on B, account created on A logs in on B, 40 concurrent token creations split across hosts land in one verified chain), and by `tests_pg/test_tokens_audit_cutover_pg.py`.

176. Model layer: provider transports and free-first default route (branch model-layer, unreleased): every model profile has a transport, `openai` (any OpenAI-compatible HTTP API, including Ollama, vLLM, the Hugging Face router and Sakana) or `transformers` (open weights run inside Meemee on CPU, CUDA or MPS, no model server). The new built-in `local-transformers` profile and `meemee models pull` fetch weights once and never download mid-request. With `MEEMEE_HF_TOKEN` set, roles without an explicit route go `local` then `inkling` on the HF router (metered Inference Providers credits; `MEEMEE_HF_FALLBACK=false` turns it off). Verified by `tests/test_model_layer.py`: HF router request shape (URL, bearer token, `thinkingmachines/Inkling-Small`) on local outage, a 402 credits-exhausted response failing once without retries, JSON extraction and error mapping for in-process models, and real CPU generation with `hf-internal-testing/tiny-random-LlamaForCausalLM` and `HuggingFaceTB/SmolLM2-135M-Instruct`, which answers through a route after the local server is down. Live Inkling inference is not verified: it needs an HF token.

177. PostgreSQL-backed email verification and password reset (branch pg-email-verification, unreleased): in PostgreSQL mode the API uses `Persistence.email_verifications` (`meemee_email_verifications`, `meemee_password_resets`), so a verification or reset link issued by one host works on any host; consuming a challenge row-locks it, so one link clicked on two hosts at once succeeds once. The cutover gains an optional `--email` group. Verified on both backends by `tests/test_email_verification_backends.py` and across two API servers by `tests/test_email_multihost_e2e.py` (signup on A, link opened on B; reset requested on B, completed on A, new password logs in on B; six concurrent clicks across hosts, one success), which fails on the previous per-host store, plus `tests_pg/test_email_cutover_pg.py`.

178. PostgreSQL-backed daily quotas and plan assignments (branch pg-quotas-entitlements, unreleased): in PostgreSQL mode the API, `meemee account-delete` and readiness use `Persistence.quotas` and `Persistence.entitlements` (migration 007), so a principal's daily job limit holds across all hosts instead of once per host, and a plan assigned on one host is enforced on all of them. Quota and plan defaults come from settings through `persistence_from_settings`. The cutover gains optional `--quotas` and `--entitlements` groups, and its verifier now compares calendar dates without a timezone shift. `meemee account-export`/`account-import` refuse to run in PostgreSQL mode instead of reading stale local files. Verified on both backends by `tests/test_quota_entitlement_backends.py` (30 concurrent submissions against a limit of 10 give exactly 10), across two API servers by `tests/test_quota_entitlement_multihost_e2e.py` (12 concurrent submissions split across hosts against a limit of 5 give five 200s and seven 429s; plan set on B enforced on A), which fails on the per-host stores, and by `tests_pg/test_quota_entitlement_cutover_pg.py`.

179. PostgreSQL-backed run history and idempotency keys (branch pg-runs, unreleased): in PostgreSQL mode the API, readiness and `meemee account-delete` use `Persistence.runs` and `Persistence.idempotency` (migration 008), so a run finished on one host is readable and listed (with cursor paging) on every host, and a `POST /v1/jobs` retried with the same `Idempotency-Key` on another host returns the first job instead of creating a second. Idempotency keys are now claimed atomically before the job is created on both backends: concurrent requests with one key get one job, the others get 409 "still in progress" until the first finishes, and a request that fails (quota, bad `run_at`) releases its claim so a retry can run; a crashed claimant frees the key after 5 minutes. Previously two concurrent same-key requests could both create jobs, even on a single host. The cutover gains optional `--runs` and `--idempotency` groups; `meemee retention-run` refuses to run in PostgreSQL mode (it prunes local SQLite files only). Verified on both backends by `tests/test_runs_idempotency_backends.py` (16 concurrent claims, one winner), across two API servers by `tests/test_runs_idempotency_multihost_e2e.py` (run on A read and paged on B, owner-scoped; retry on B returns A's job; 10 concurrent same-key requests split across hosts create one job), which fails on the per-host stores, and by `tests_pg/test_runs_idempotency_cutover_pg.py`.

180. PostgreSQL-backed webhook subscriptions and delivery outbox (branch pg-webhooks, unreleased): in PostgreSQL mode the API, `meemee worker`, `meemee webhook-worker`, readiness and account deletion use `Persistence.webhooks` (migration 009), so a subscription created on one API host is listed, counted against the plan limit, paused, rotated, tested and deleted through any host, jobs finished by a worker on another machine queue its deliveries, and any number of dispatchers share one outbox (`FOR UPDATE SKIP LOCKED`, so no delivery is sent twice). Secrets use the same encryption key and context as SQLite; payloads stay text so signatures cover the exact bytes. `api.py` no longer reaches into the SQLite connection (new store methods: `active_count`, `list_subscriptions`, `enqueue_test`, `get_delivery`, and the delivery list/replay/cleanup/metrics helpers). Two fixes on both backends: `GET /v1/webhook-deliveries/{id}` found only deliveries among the newest 500, and cleanup left orphaned attempt rows. The cutover gains an optional `--webhooks` group. Verified on both backends by `tests/test_webhook_backends.py` (four store handles draining 40 deliveries from 8 threads claim each exactly once; signed delivery through the dispatcher), across two API servers, a worker host and two dispatcher hosts with a real local HTTPS receiver by `tests/test_webhooks_multihost_e2e.py` (subscription made on A managed on B; job submitted on B, run on the worker host, delivered after one 500 retry exactly once with a valid signature and not to another tenant; 12 test events split across hosts delivered exactly once each; a secret rotated on A signs the next delivery), which fails on the per-host stores, and by `tests_pg/test_webhooks_cutover_pg.py`.

181. PostgreSQL-backed companion state (branch pg-companion, unreleased): in PostgreSQL mode the API, `meemee companion` commands, the companion check-in worker, readiness, customer export and account deletion use `Persistence.companion` (migration 010), so a companion user, persona, facts, conversations, message model traces and check-ins created through one host are served and changed through every host, and several companion workers share one check-in queue (`FOR UPDATE SKIP LOCKED`, one sender per check-in). Fact search matches like SQLite FTS5 (any query word, no stemming) but ranks with `ts_rank_cd`; the order of equally relevant facts may differ from SQLite. The cutover gains an optional `--companion` group. Verified on both backends by `tests/test_companion_backends.py` (four store handles draining 20 due check-ins from 8 threads claim each once), across two API servers by `tests/test_companion_multihost_e2e.py` (user made on A read on B; facts added on both, searched on A, retired on A, gone on B; a check-in planned on A is listed on B, not planned twice, and cancelled from B), which fails on the per-host store, and by `tests_pg/test_companion_cutover_pg.py` (a user's full export is identical before and after the copy). Companion chat against a live model across hosts is not covered by the e2e test.
182. PostgreSQL-backed personal model and connected context (branch pg-personal-context, unreleased): in PostgreSQL mode the API personal-model routes, the agent (API host and `meemee worker`), the companion engine, the scheduled reflection worker, readiness and account deletion use `Persistence.personal_model` and `Persistence.context` (migration 011), so a claim written through one host is listed, corrected and deleted through every host, and every process assembles personal context from the same connected-source records. Concurrent writes of one claim are serialized with an advisory lock; one active claim per (owner, kind, title) is enforced by a partial unique index. Parity fix on SQLite too: the old `UNIQUE(owner_id,kind,title,status)` allowed only one superseded row per claim, so the second correction of the same claim raised IntegrityError; existing files are rebuilt on open with a partial unique index instead. Context search matches plain word queries like SQLite FTS5 (every word, no stemming) and ranks with `ts_rank_cd`; FTS5 operator syntax is not interpreted in PostgreSQL. The cutover gains optional `--personal` and `--context` groups. Verified on both backends by `tests/test_personal_context_backends.py` (16 concurrent writers of one claim leave one active row; legacy SQLite file upgrade), across two API servers by `tests/test_personal_model_multihost_e2e.py` (claim made on A listed on B, corrected on B then on A, deleted on B and gone on A), which fails on the per-host store, and by `tests_pg/test_personal_context_cutover_pg.py`. Connected context has no HTTP route, so its cross-host behavior is covered at the store level, not by the two-host e2e test; reflection against a live model across hosts is not covered.
183. PostgreSQL-backed monitors and reflection schedule (branch pg-monitors-reflection, unreleased): in PostgreSQL mode the API monitor routes, readiness, account deletion and `meemee reflection-worker` use `Persistence.monitors` and `Persistence.reflection_schedule` (migration 012), so a monitor created through one host is listed, inspected and cancelled through every host, and reflection workers on several hosts share one set of per-owner watermarks instead of each re-reflecting every owner. `evaluate` locks the owner's active monitors, so concurrent evaluations never count past `max_fires`. The cutover gains optional `--monitors` and `--reflection` groups. Verified on both backends by `tests/test_monitors_reflection_backends.py` (20 concurrent evaluations of a three-fire monitor fire exactly three times), across two API servers by `tests/test_monitors_multihost_e2e.py` (monitor made on A listed and cancelled on B, events visible on both; a second reflection worker on its own connection pool skips an owner the first already covered), which fails on the per-host store, and by `tests_pg/test_monitors_reflection_cutover_pg.py`. Still thin: no product code path calls `MonitorStore.evaluate` yet (monitors are created, listed and cancelled, but nothing feeds them events), and two reflection workers that start the same owner at the same instant can both reflect it once (no claim lock; claims dedupe on evidence).
184. PostgreSQL-backed account-deletion ledger, browser session records and takeover notices (branch pg-deletion-browser, unreleased): in PostgreSQL mode the API, readiness and `build_account_purger` use `Persistence.deletion_ledger`, `Persistence.browser_sessions` and `Persistence.browser_notices` (migrations 013 and 014). The deletion ledger is shared, so any host reports and resumes a deletion and a partial unique index keeps one open deletion per principal across hosts. Browser session, takeover and event records are shared, but the live Chromium session stays in the process that opened it: each session row records its `host_id` (`MEEMEE_HOST_ID`, or hostname plus a digest of the data directory), a restarting host marks only its own open sessions lost (before, any restart marked every open session lost), and a close, takeover or release that reaches another host fails with "browser session is live on host X" instead of a misleading success. Takeover notices form one queue; delivery claims notices with a lease and `FOR UPDATE SKIP LOCKED`, and the delivery loop is now one function shared by both backends. SQLite session files gain the `host_id` column on open. The cutover gains optional `--deletions`, `--browser` and `--notices` groups (an old browser-sessions file must be opened once by this version before the copy, so it has `host_id`). Verified on both backends by `tests/test_deletion_ledger_backends.py` (16 concurrent opens give one deletion) and `tests/test_browser_records_backends.py` (host-scoped restart, notice delivery and cancellation, legacy file upgrade); across two API servers by `tests/test_deletion_ledger_multihost_e2e.py` (a deletion run on A is reported by B) and `tests/test_browser_records_multihost_e2e.py` (a session held by A is listed on B, B names A when asked to close or release it, B's startup leaves it open, and deletion on B erases it), both of which fail on the per-host stores; and by `tests_pg/test_deletion_ledger_cutover_pg.py` and `tests_pg/test_browser_records_cutover_pg.py`. Not shared: the live browser itself and `browser-profiles/` (cookies on disk), so takeover links and browser requests must reach the owning host (sticky routing).
185. PostgreSQL-backed secret vault and key rotation (branch pg-vault-rotation, unreleased): in PostgreSQL mode `meemee vault-put`, `vault-list` and `verify-webhook` use the shared `meemee_vault_secrets` table (migration 015, same AES-256-GCM record format as vault.sqlite3), so a secret stored from one host is readable on every host. `meemee rotate-encryption-key` in PostgreSQL mode re-encrypts every vault secret and every PostgreSQL webhook subscription secret in one transaction (all rows or none; a wrong old key changes nothing); before, it re-encrypted only local files and left the shared webhook secrets under the old key, which would have broken every delivery after the key swap. The cutover gains an optional `--vault` group. Verified by `tests/test_vault_backends.py` (both backends; PG rotation, delivery secret intact after rotation, failed rotation leaves rows unchanged), `tests/test_vault_multihost_e2e.py` (separate CLI processes with separate data directories: put on A, listed on B, rotated from B, webhook secrets readable only with the new key, listed on A with the new key), which fails on the per-host vault, and `tests_pg/test_vault_cutover_pg.py`. Deployment requirement for PostgreSQL mode: live browser sessions and `browser-profiles/` stay on the host that opened them, so the load balancer must route `/v1/browser/*` and `/browser/takeover*` for a session to that host (sticky routing, for example by session or takeover id); other hosts answer with the owning host id.
187. PostgreSQL operator tools (branch pg-operator-tools, unreleased): in PostgreSQL mode `meemee account-export`, `account-import`, `retention-run`, `audit-anchor`, `audit-anchor-verify` and `audit-prune` now act on the shared database from any host (new `meemee_persist_pg/operator_tools.py`, no new migration) instead of refusing to run; this supersedes the refusals noted in items 178 and 179 and the audit-anchor refusal on main. Exports keep the `meemee.account.v1` envelope and field encodings, so an export from either backend imports into either backend (job IDs change to the dashed UUID form in PostgreSQL). Import runs collision checks and all inserts in one transaction, so a collision or error writes nothing. Retention applies the same windows to the shared tables in one transaction (and also removes the attempt rows of pruned webhook deliveries); the audit chain is never touched. Anchors take the head and verify the chain from one snapshot; `audit-prune` holds the audit append lock, so no host can append between the prefix delete and the chain-base update, and re-checks the anchored entry under that lock. An anchor made on SQLite still verifies and prunes after cutover. Verified by `tests_pg/test_operator_tools_pg.py` (SQLite -> PostgreSQL -> SQLite export round trip, one-transaction import, per-table retention, tamper and stale-anchor refusal, SQLite anchor after cutover), and across two API servers by `tests/test_operator_tools_multihost_e2e.py` (operator on a third host with an empty data dir exports jobs/runs made on both hosts, retention removes a run both hosts then 404, prune while both hosts append 16 entries leaves one verified chain), which fails both on main's refusing CLI and on the per-host SQLite paths.
186. Shared model layer (branch shared-model-layer, unreleased): Meemee now uses the same `instinct_models` package as Atlas and Sugarcode (https://github.com/uditakankananonononono/shared-models), vendored under `meemee/_vendor/instinct_models` and pinned by commit in `meemee/_vendor/INSTINCT_MODELS_PIN` (refresh with `scripts/sync_instinct_models.sh <commit>`). A new `shared` model profile (`transport: "instinct"`) routes Needle -> Ornith -> Inkling and can sit in any route, e.g. `MEEMEE_MODEL_ROUTES="chat=shared,local"`; it reports unavailable until `MEEMEE_SHARED_ORNITH_URL`/`MEEMEE_SHARED_ORNITH_MODEL` or `MEEMEE_SHARED_INKLING_URL` is set. Shared-layer calls are private by default and never reach the hosted (metered) Hugging Face router unless `MEEMEE_SHARED_ALLOW_HOSTED=true`. The shared layer also carries an optional Jev evaluation provider (TypeSafe AI's hosted System One model, key-gated and paid, OFF by default): `meemee.shared_models.build_jev()` enables it with `MEEMEE_JEV_API_KEY` or `JEV_API_KEY`; see docs/models.md. `meemee.shared_models.MeemeeDataset` and `train_meemee_needle` train a Meemee-only Needle LoRA adapter from owner-confirmed examples (unconfirmed rows and rows tagged for another product are dropped). Verified by `tests/test_shared_models.py` with fake transports; no live Ornith, Inkling or Needle model was run (no GPU or server here, and the cactus-needle engine download returned 404 at check time).

## Thin (0)

Nothing is classified as thin. A capability is either implemented and tested at its stated boundary below, or listed as missing.

## Missing, not claimed

Live WhatsApp and iMessage delivery over real provider networks is unverified: the adapters and durable delivery queue are implemented and config-gated, but no provider account exists yet. Live PostgreSQL integration is not verified in this environment; target deployments must run the unskipped PostgreSQL suite and preflight. Built-in email/password signup and login are implemented; external OIDC remains optional. Email verification, password reset, customer data export and product-wide account deletion are implemented. Browser challenges are detected and handed to a person through the live takeover view; live sessions are held in the API process and do not survive a restart. Tools that opt into process isolation can also be forcibly killed on cancel or timeout; tools that are not picklable, or that are not marked for isolation, still rely on cooperative cancellation. Multi-region failover, online dual-write migration and logical replication remain deployment/infrastructure work and are not claimed.

The SQLite single-host shape and PostgreSQL memory, owner-scoped jobs and shared rate limiting are stated at their tested boundaries. SSE and authenticated WebSocket job streams are implemented. No missing item is represented as shipped.

## Install

Python 3.10+ is required.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
meemee init --data-dir ~/.meemee --env-file .env
set -a; . ./.env; set +a
meemee preflight --require-model
pytest
```

`meemee init` refuses to overwrite an existing `.env` or initialize a non-empty data directory. It writes the generated credentials only to an owner-readable file and never echoes them.

The default model endpoint is Ollama's OpenAI-compatible API:

```bash
ollama pull qwen2.5-coder:14b
ollama serve
meemee run "Find actively maintained Python agent frameworks on GitHub and compare them"
```

Search GitHub directly without a model:

```bash
meemee scout "autonomous agent" --language Python --min-stars 100 --limit 10
```

Set `MEEMEE_GITHUB_TOKEN` to avoid GitHub's low anonymous search limit. Never commit it.

Start the API:

```bash
meemee serve --host 127.0.0.1 --port 8787
curl http://127.0.0.1:8787/health
curl -X POST http://127.0.0.1:8787/v1/runs -H 'content-type: application/json' \
  -d '{"goal":"Find the best current Python repository for local agents"}'
```

File writes are denied unless the caller opts in with `--approve-writes` or `approve_writes: true`. Refused calls are listed in the result's `approvals_required` with the approval body that would allow them. Queued jobs (`POST /v1/jobs`) have no per-request approval; they may use an approval-gated tool only through the owner's persistent grant (`PUT /v1/approvals/{principal}`). This coarse v1 switch is not a substitute for user-scoped production authorization.

## Architecture

- `agent.py`: bounded perceive-decide-act loop with evidence-preserving tool events.
- `tools/`: typed registry, GitHub scout, browser automation, command execution and workspace I/O.
- `memory.py`: SQLite event store and lexical full-text retrieval.
- `plan_store.py`: durable versioned DAG plans and edit history.
- `jobs.py`: durable single-node scheduled queue and append-only event log.
- `llm.py`: OpenAI-compatible transport.
- `companion/`: per-user profiles and persona, durable fact memory, conversation persistence, proactive check-in scheduler and channel adapters.
- `cli.py` and `api.py`: interfaces over the same runtime.

A model statement is never treated as proof that work happened. Tool returns are stored separately, side effects require approval, and paths stay inside the configured workspace.


## Advanced operation

### Live PostgreSQL check

```bash
pip install -e '.[dev,postgresql]' pgserver
python scripts/pg_live_check.py                       # throwaway local server
python scripts/pg_live_check.py --timezone Asia/Kolkata   # test a non-UTC server
python scripts/pg_live_check.py --dsn postgresql://user:pw@host/db
```

The DSN role must be allowed to `CREATE DATABASE`; every live test runs in its own throwaway database. Exit code 0 means every PostgreSQL test ran and passed; any skip fails the check.

### Forced interruption for blocking tools

Cooperative cancellation cannot stop a tool stuck in blocking or native code. Mark such a tool for process isolation:

```python
from meemee.isolation import IsolationPolicy
from meemee.tools.base import Tool

class OcrTool(Tool):
    name = "ocr.page"
    isolation = IsolationPolicy(timeout_s=120, grace_s=2)
    ...
```

`ToolRegistry.execute` then runs the call in a spawned child. Cancelling the job or passing `timeout_s` sends SIGTERM, then SIGKILL after `grace_s`; the call returns `ToolResult(ok=False, error="tool cancelled")` or a hard-timeout error. The tool class must be importable at module level, the tool instance, its arguments and its return value must be picklable, and each call pays a fresh interpreter start (about 0.3 s per call measured in the pb7 sandbox). `run_isolated` / `run_isolated_sync` in `meemee/isolation.py` can also be used directly.


Set `MEEMEE_API_TOKEN` in production-facing environments. Run one or more durable workers with `meemee worker`. Schedule work through `POST /v1/jobs` with an ISO 8601 `run_at`; workers atomically claim due jobs. Shell and local Git mutations are side effects and still require agent-run approval. Commands are direct argv calls, never `shell=True`, and only configured executables can run. Git commits name explicit paths and never push.

Before serving production traffic, run `meemee release-audit .` and `meemee preflight --require-model`; it exits nonzero on required failures and prints JSON suitable for CI/deployment gates. Model reachability is warning-only without `--require-model`.

Browser setup: `pip install -e .[browser]` then `playwright install chromium`. Vault setup: run `meemee vault-key`, store the output as `MEEMEE_VAULT_KEY` outside the repo, and add secrets with `meemee vault-put NAME`. Kubernetes expects a separately managed `meemee-secrets` Secret; no plaintext secret manifest is committed.


Cancellation contract: cancelling an already-cancelled job is idempotent and returns its terminal cancelled state without appending another event. SSE streams emit that durable cancelled event and then close.


Current verification is tracked in [STATUS.md](STATUS.md). The operator console is mounted at `/console/`. Readiness reports model health but, by default, does not fail the API solely because an optional/local model process is offline; set `MEEMEE_READINESS_REQUIRE_MODEL=true` where model availability must gate traffic.


Concurrency contract: all shared SQLite connections are serialized at the store boundary. Token create/authenticate/revoke and audit append/verify/list use reentrant locks plus a five-second SQLite busy timeout. Load regression tests execute 2,000 parallel authentications and 1,000 parallel audit appends with concurrent verification.


Application lifecycle uses FastAPI lifespan context rather than deprecated event hooks. Shutdown drains active runs and closes model transport from the lifespan finalizer, with a direct regression test.

## Jev API status (2026-09-26)

Vercel currently lists `typesafe-ai/jev` without Free Tier eligibility; the gateway route is paid. The TypeSafe direct API is also paid. A browser playground, if available from an official provider, is not free API access. The previously referenced `thejevai.com` could not be verified as TypeSafe AI's official site; do not use it for API keys, billing, or model calls. Official direct API: https://api.typesafe.ai/v1/systemone; keys: https://console.typesafe.ai/keys (https://docs.typesafe.ai/api). Gateway: https://ai-gateway.vercel.sh/typesafe/v1/systemone (https://vercel.com/docs/ai-gateway/sdks-and-apis/typesafe). Both routes stay OFF until a key is explicitly configured. Set `AI_GATEWAY_API_KEY` (or `INSTINCT_AI_GATEWAY_API_KEY`) for the preferred gateway route, or `JEV_API_KEY` (or `INSTINCT_JEV_API_KEY`) for the direct alternate; if both are present the gateway wins. Do not add keys to git. Vercel eligibility: https://vercel.com/ai-gateway/models/providers/typesafe-ai and https://vercel.com/docs/ai-gateway/pricing.
