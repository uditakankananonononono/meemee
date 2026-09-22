# Meemee

Meemee is Udita's private, local-first agent runtime. It turns a goal into an inspectable plan, gives a model a bounded set of real tools, records every result, and stops honestly when it finishes or cannot continue. This is a working commercial-grade single-host runtime, not a claim to be finished general intelligence. Read [STATUS.md](STATUS.md) for the exact verified, thin and missing ledger, and [CHANGELOG.md](CHANGELOG.md) for release history.

## Verified in v0.30.0 (52)

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

## Thin (0)

Nothing is classified as thin. A capability is either implemented and tested at its stated boundary below, or listed as missing.

## Missing, not claimed

Automated model-driven replanning policies; semantic/embedding memory and reranking; core runtime selection for the additive PostgreSQL stores and an automated SQLite-to-PostgreSQL data-copy CLI; end-user account administration UI (interactive OIDC login/session/logout, bearer auth and scoped API tokens are implemented); browser human takeover, managed downloads, challenge handoff and per-site policy; remote Git push and pull-request operations; a permissions UI; cross-host/cross-pod rate limiting (single-host multi-process limiting is implemented); WebSocket streaming (resume-safe SSE is implemented); forced mid-tool cancellation (cooperative between-step cancellation is implemented); and a core-wired shared database/queue suitable for Kubernetes replicas (the additive PostgreSQL package exists, but the runtime still uses SQLite).

The existing deterministic goal decomposition, lexical FTS, scoped/revocable API token system, browser automation, single-host shared limiter, SQLite queue, and cursor event API and SSE stream remain useful internal or single-node features, but they are not presented as completed versions of the advanced capabilities above.

Commercial operation, credentials, backup, recovery, upgrades and incident steps are in [OPERATIONS.md](OPERATIONS.md).

## Install

Python 3.10+ is required.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env
pytest
```

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

Browser setup: `pip install -e .[browser]` then `playwright install chromium`. Vault setup: run `meemee vault-key`, store the output as `MEEMEE_VAULT_KEY` outside the repo, and add secrets with `meemee vault-put NAME`. Kubernetes expects a separately managed `meemee-secrets` Secret; no plaintext secret manifest is committed.


Cancellation contract: cancelling an already-cancelled job is idempotent and returns its terminal cancelled state without appending another event. SSE streams emit that durable cancelled event and then close.


Current verification is tracked in [STATUS.md](STATUS.md). The operator console is mounted at `/console/`. Readiness reports model health but, by default, does not fail the API solely because an optional/local model process is offline; set `MEEMEE_READINESS_REQUIRE_MODEL=true` where model availability must gate traffic.


Concurrency contract: all shared SQLite connections are serialized at the store boundary. Token create/authenticate/revoke and audit append/verify/list use reentrant locks plus a five-second SQLite busy timeout. Load regression tests execute 2,000 parallel authentications and 1,000 parallel audit appends with concurrent verification.


Application lifecycle uses FastAPI lifespan context rather than deprecated event hooks. Shutdown drains active runs and closes model transport from the lifespan finalizer, with a direct regression test.
