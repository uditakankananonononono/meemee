# Meemee

Meemee is Udita's private, local-first agent runtime. It turns a goal into an inspectable plan, gives a model a bounded set of real tools, records every result, and stops honestly when it finishes or cannot continue. This is a working commercial-grade single-host runtime, not a claim to be finished general intelligence. Read [STATUS.md](STATUS.md) for the exact verified, thin and missing ledger, and [CHANGELOG.md](CHANGELOG.md) for release history.

## Verified in v0.60.0 (78)

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

## Thin (0)

Nothing is classified as thin. A capability is either implemented and tested at its stated boundary below, or listed as missing.

## Missing, not claimed

core runtime selection for the additive PostgreSQL stores and an automated SQLite-to-PostgreSQL data-copy CLI; end-user account administration UI (interactive OIDC login/session/logout, bearer auth and scoped API tokens are implemented); interactive browser human takeover beyond explicit challenge detection/handoff; a permissions UI; cross-host/cross-pod rate limiting (single-host multi-process limiting is implemented); WebSocket streaming (resume-safe SSE is implemented); forced mid-tool cancellation (cooperative between-step cancellation is implemented); and a core-wired shared database/queue suitable for Kubernetes replicas (the additive PostgreSQL package exists, but the runtime still uses SQLite).

The existing deterministic goal decomposition, lexical FTS, scoped/revocable API token system, browser automation, single-host shared limiter, SQLite queue, and cursor event API and SSE stream remain useful internal or single-node features, but they are not presented as completed versions of the advanced capabilities above.

Commercial operation, credentials, backup, recovery, upgrades and incident steps are in [OPERATIONS.md](OPERATIONS.md).

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

File writes are denied unless the caller opts in with `--approve-writes` or `approve_writes: true`. This coarse v1 switch is not a substitute for user-scoped production authorization.

## Architecture

- `agent.py`: bounded perceive-decide-act loop with evidence-preserving tool events.
- `tools/`: typed registry, GitHub scout, browser automation, command execution and workspace I/O.
- `memory.py`: SQLite event store and lexical full-text retrieval.
- `plan_store.py`: durable versioned DAG plans and edit history.
- `jobs.py`: durable single-node scheduled queue and append-only event log.
- `llm.py`: OpenAI-compatible transport.
- `cli.py` and `api.py`: interfaces over the same runtime.

A model statement is never treated as proof that work happened. Tool returns are stored separately, side effects require approval, and paths stay inside the configured workspace.


## Advanced operation

Set `MEEMEE_API_TOKEN` in production-facing environments. Run one or more durable workers with `meemee worker`. Schedule work through `POST /v1/jobs` with an ISO 8601 `run_at`; workers atomically claim due jobs. Shell and local Git mutations are side effects and still require agent-run approval. Commands are direct argv calls, never `shell=True`, and only configured executables can run. Git commits name explicit paths and never push.

Before serving production traffic, run `meemee release-audit .` and `meemee preflight --require-model`; it exits nonzero on required failures and prints JSON suitable for CI/deployment gates. Model reachability is warning-only without `--require-model`.

Browser setup: `pip install -e .[browser]` then `playwright install chromium`. Vault setup: run `meemee vault-key`, store the output as `MEEMEE_VAULT_KEY` outside the repo, and add secrets with `meemee vault-put NAME`. Kubernetes expects a separately managed `meemee-secrets` Secret; no plaintext secret manifest is committed.


Cancellation contract: cancelling an already-cancelled job is idempotent and returns its terminal cancelled state without appending another event. SSE streams emit that durable cancelled event and then close.


Current verification is tracked in [STATUS.md](STATUS.md). The operator console is mounted at `/console/`. Readiness reports model health but, by default, does not fail the API solely because an optional/local model process is offline; set `MEEMEE_READINESS_REQUIRE_MODEL=true` where model availability must gate traffic.


Concurrency contract: all shared SQLite connections are serialized at the store boundary. Token create/authenticate/revoke and audit append/verify/list use reentrant locks plus a five-second SQLite busy timeout. Load regression tests execute 2,000 parallel authentications and 1,000 parallel audit appends with concurrent verification.


Application lifecycle uses FastAPI lifespan context rather than deprecated event hooks. Shutdown drains active runs and closes model transport from the lifespan finalizer, with a direct regression test.
