# Meemee operator guide

## Production baseline

Run Meemee behind TLS at a reverse proxy or ingress. Set these as secrets, never in source control:

- `MEEMEE_API_TOKEN`: a long random bootstrap administrator token. Use it only to mint scoped tokens, then keep it offline.
- `MEEMEE_MODEL_API_KEY`: model provider credential, or `local` for an isolated local endpoint.
- `MEEMEE_VAULT_KEY`: output of `meemee vault-key`. Loss of this key makes vault records unrecoverable.
- `MEEMEE_GITHUB_TOKEN`: optional, required for sustained GitHub search usage.

Mount `MEEMEE_DATA_DIR` on persistent encrypted storage. The API and workers must share it only on one host. SQLite is a supported single-node deployment. Kubernetes replicas need a future shared database/queue and are listed as missing rather than claimed production support.

## First start and scoped credentials

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

## Backups and recovery

Stop API/workers or use SQLite's online backup API before copying databases. Back up all files under `MEEMEE_DATA_DIR`, plus the vault key stored separately. Test restoration on a separate host. Never restore two active writers from the same copied SQLite state.

Recommended daily retention: 7 daily, 4 weekly, 6 monthly encrypted backups. Monitor disk usage, failed job events, HTTP 429/5xx rates, worker liveness, and model latency.

## Upgrades

1. Back up `MEEMEE_DATA_DIR` and the external vault key.
2. Read the release ledger and missing list.
3. Install the new version in a fresh environment.
4. Run `pytest` from source or the release smoke checks.
5. Start one API process, verify `/ready`, then start workers.
6. Keep the prior image/package available for rollback.

Schema creation is idempotent. There is not yet a formal migration framework, so cross-version destructive schema changes are not supported and remain missing.

## Incident steps

- Leaked API token: revoke its ID, inspect `last_used_at`, issue a replacement.
- Leaked bootstrap token: replace the environment secret and restart all API processes.
- Leaked vault key: rotate the key by exporting and re-encrypting secrets on an offline trusted host; treat all stored values as exposed.
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
