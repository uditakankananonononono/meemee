# Meemee operator guide

## Production baseline

Run Meemee behind TLS at a reverse proxy or ingress. Set these as secrets, never in source control:

Set `MEEMEE_TRUSTED_HOSTS` to the comma-separated public/internal hostnames accepted by the API. Keep the default local names only for local development. After confirming every route is HTTPS, set `MEEMEE_HSTS_ENABLED=true`; the default max age is one year and includes subdomains. Do not enable HSTS on a hostname that must still serve HTTP.

- `MEEMEE_API_TOKEN`: a long random bootstrap administrator token. Use it only to mint scoped tokens, then keep it offline.
- `MEEMEE_MODEL_API_KEY`: model provider credential, or `local` for an isolated local endpoint.
- `MEEMEE_VAULT_KEY`: output of `meemee vault-key`. Required at API and webhook-worker startup; it encrypts both vault records and webhook signing secrets. Loss of this key makes those records unrecoverable.
- `MEEMEE_GITHUB_TOKEN`: optional, required for sustained GitHub search usage.

Mount `MEEMEE_DATA_DIR` on persistent encrypted storage. The API and workers must share it only on one host. SQLite is a supported single-node deployment. Kubernetes replicas need a future shared database/queue and are listed as missing rather than claimed production support.

## First start and scoped credentials

Initialize a new instance without hand-writing secrets:

```bash
meemee init --data-dir ~/.meemee --env-file .env
set -a; . ./.env; set +a
meemee preflight --require-model
```

Initialization refuses existing config and non-empty data targets. The generated env file is mode `0600` and contains the bootstrap and vault secrets; store and back it up as a secret, never commit it.

```bash
export MEEMEE_API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
meemee serve --host 127.0.0.1 --port 8787
```

In another terminal, create a least-privilege operations token:

```bash
curl -sS -X POST http://127.0.0.1:8787/v1/tokens \
  -H "Authorization: Bearer $MEEMEE_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"daily-operator","scopes":["runs:write","jobs:read","jobs:write"]}'
```

The raw token is returned once. Store it in a secret manager. Meemee stores only its digest. Revoke it with `DELETE /v1/tokens/{id}` using an admin token.

Before building a release, run `meemee release-audit .`; after building the wheel, run `meemee package-audit dist/meemee_agent-*.whl` and install it into a clean virtual environment for CLI smoke checks. it fails on missing commercial assets, version drift and explicit stub markers. Before starting API or worker traffic, run `meemee preflight --require-model`. Treat its JSON output as a deployment gate: exit 0 means all required checks passed; exit 1 identifies each failed check without exposing secret values. Omit `--require-model` only when temporary model downtime is acceptable.

## Backups and recovery

Stop API/workers or use SQLite's online backup API before copying databases. Back up all files under `MEEMEE_DATA_DIR`, plus the vault key stored separately. Test restoration on a separate host. Never restore two active writers from the same copied SQLite state.

Recommended daily retention: 7 daily, 4 weekly, 6 monthly encrypted backups. Monitor disk usage, failed job events, HTTP 429/5xx rates, worker liveness, and model latency.

## Upgrades

1. Back up `MEEMEE_DATA_DIR` and the external vault key.
2. Read the release ledger and missing list.
3. Install the new version in a fresh environment. For v0.35+, provide the existing `MEEMEE_VAULT_KEY` on first start; webhook subscription secrets are upgraded from plaintext in one transaction. Back up first and do not interrupt that first startup.
4. Run `pytest` from source or the release smoke checks.
5. Start one API process, verify `/ready`, then start workers.
6. Keep the prior image/package available for rollback.

Schema creation is idempotent. There is not yet a formal migration framework, so cross-version destructive schema changes are not supported and remain missing.

## Incident steps

- Leaked API token: revoke its ID, inspect `last_used_at`, issue a replacement.
- Leaked bootstrap token: replace the environment secret and restart all API processes.
- Leaked vault key: rotate vault records and webhook signing secrets on an offline trusted host, then rotate receiver secrets through the API; treat all stored values as exposed. A built-in all-record key-rotation command remains missing.
- Stuck queued work: inspect `/v1/jobs/{id}` and `/events`, cancel queued work, then check worker/model logs.
- Repeated browser challenge: stop automation for that site. Meemee does not claim challenge bypass.

## OIDC identity provider

For workforce or customer access, configure all three together:

```bash
MEEMEE_OIDC_ISSUER=https://login.example.com/
MEEMEE_OIDC_AUDIENCE=meemee-api
MEEMEE_OIDC_JWKS_URL=https://login.example.com/.well-known/jwks.json
```

The issuer and JWKS URL must be HTTPS on the same host. Meemee verifies the signature, algorithm, issuer, audience, expiry, issued-at time and subject. Configure roles with `MEEMEE_OIDC_ROLE_SCOPES`; unknown roles receive no rights. Rotate signing keys at the identity provider; JWKS keys are cached for five minutes. Keep a break-glass bootstrap token offline. Meemee accepts access tokens as bearer tokens; it does not implement an interactive browser login screen yet.

## Supported database and scale boundary

SQLite/WAL is the supported production database for a single host. Run multiple worker processes on that host if needed; atomic queue claims prevent duplicate claims. Do not mount the database over NFS and do not run multiple Kubernetes pods against one ReadWriteOnce SQLite volume. Meemee does not yet claim a supported PostgreSQL path or multi-node control plane.

Before an upgrade, run `meemee backup /secure/backups/meemee-YYYYMMDD` and `meemee backup-verify` on the result. `meemee db-migrate` applies forward-only checksummed migrations and refuses edited migration history. Backup manifests contain file size and SHA-256, and verification also runs SQLite integrity checks. Restore into a stopped instance, verify it, then start one API process before workers.

## Observability

`GET /metrics` is Prometheus format and requires an admin bearer token. Scrape it through a protected internal route; do not expose it publicly. Metrics include request count by route/status, latency histograms, in-flight requests, agent outcomes and jobs created. Set `MEEMEE_LOG_JSON=true` for structured UTC JSON logs and `MEEMEE_LOG_LEVEL` to `INFO`, `WARNING` or `ERROR`. Ship stdout to your log platform and alert on sustained 5xx responses, p95 latency, failed agent runs, worker absence and disk pressure. Request responses carry `X-Request-ID`; preserve it at the proxy and use it to correlate logs.

## Interactive login

Configure the authorization endpoint, token endpoint, client ID/secret, exact HTTPS redirect URI and a random `MEEMEE_SESSION_KEY` of at least 32 characters. Register the same redirect URI at the identity provider. The flow uses Authorization Code with PKCE S256, signed 10-minute state, nonce, CSRF state comparison, and an 8-hour Secure/HttpOnly/SameSite=Lax session cookie. Keep the application behind HTTPS. Rotate the session key to invalidate all sessions. The home page provides sign-in status and logout; full account administration remains external at the identity provider.

## Rate limits

The limiter uses a dedicated SQLite/WAL database and atomic transactions, so all API processes on the supported single host share one limit. Authenticated callers are isolated by a non-reversible prefix of the bearer-token digest; unauthenticated callers use the directly connected client IP. Do not trust forwarded headers in Meemee itself: terminate TLS at a controlled proxy and enforce edge limits there too. Health/readiness checks are exempt. Responses include `RateLimit-Limit`, `RateLimit-Remaining`, and `RateLimit-Reset`; blocked requests also include `Retry-After`. Cross-host deployments still require a future distributed limiter.

## Live job progress

Use `GET /v1/jobs/{id}/stream` with `Accept: text/event-stream` and a token carrying `jobs:read`. Each event has a durable numeric ID. Reconnect with the `Last-Event-ID` header to replay only later events; the server closes after a terminal done/failed event. Heartbeat comments keep idle connections alive. Disable response buffering for this route at the reverse proxy; Meemee also sends `X-Accel-Buffering: no` and `Cache-Control: no-cache, no-transform`.

## Idempotent job submission

Send a unique `Idempotency-Key` header when creating a job. Retries by the same principal with the same key and identical body return the original job ID instead of creating duplicates. Reusing the key with a changed body returns HTTP 409. Keys are scoped to principal and route and retained for 24 hours. Use a UUID generated once per logical submission, not once per HTTP retry.

## Data retention

Run `meemee retention-run` from cron after backups. Defaults retain terminal jobs/events 30 days and memories 90 days; active jobs are never removed. Expired idempotency and rate-window records are cleaned. The tamper-evident audit chain is retained intact because deleting its prefix without externally signed anchors would break verification; archive/export with signed anchors is still missing. Always review legal and contractual retention duties before changing windows.

## Webhook alerts and recovery

Alert when `meemee_webhook_success_rate` falls below 0.95 for 10 minutes, `meemee_webhook_oldest_queued_seconds` exceeds 300, any `meemee_webhook_suspended_subscriptions` is nonzero, or failed outbox depth grows. The circuit breaker suspends a subscription after five terminal failures. Operators should inspect the attempt timeline, fix the endpoint, wait the configured cooldown (`MEEMEE_WEBHOOK_BREAKER_COOLDOWN_SECONDS`, default 300), send a test delivery, then resume. Do not bypass TLS or SSRF checks to clear an alert.

## Product plans and billing boundary

`GET /v1/product/plans` is the authoritative machine-readable catalog. Starter, team and business plans currently enforce daily queued-job and active-webhook limits; admin assignment through `PUT /v1/entitlements/{principal}` updates the atomic job quota and records an audit event. Meemee does not process payments, publish prices or infer billing state. Connect a licensed external billing system to the admin entitlement endpoint only after verifying its signed events.
