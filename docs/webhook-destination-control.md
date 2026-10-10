# Companion webhook destination control

This unit adds a real owner-only HTTPS verification flow and a fail-closed
check-in send-intent gate. It does not grant message consent; existing enabled
check-in preferences, exact channel/address binding and quiet hours still apply.
Local delivery is unchanged. WhatsApp and iMessage cannot be verified with the
current generic outbound adapters, so check-in send intent cancels them. No
legacy destination is automatically trusted.

## Receiver protocol

Implement a POST receiver that explicitly handles `kind: meemee.destination_control`.
The request contains only `kind`, `challenge_id`, and an unpredictable `nonce`.
Return HTTP 200 and JSON containing exactly `challenge_id` and `nonce`, matching
both strings. Never log nonce values. Reject unknown message kinds rather than
reflecting arbitrary requests. Receive ordinary `companion.message` payloads using
the existing webhook channel protocol. Configure HTTPS with a trusted certificate.

Authenticated principal P uses:

- POST `/v1/companion/users/P/destinations/webhook/verify`, JSON `destination`.
  Requires companion:write. Server contacts that exact endpoint and verifies its
  response. The caller never receives a nonce and cannot supply proof or a method.
- GET `/v1/companion/users/P/destinations`, companion:read, lists grant metadata.
- DELETE `/v1/companion/users/P/destinations/GRANT_ID`, companion:write, revokes
  that grant. If several grants exist for one destination, revoke each to disable
  all its grants, or disable/change the check-in preference.

Admin profile-management access does not bypass principal-equals-P for these
routes. All cross-owner/missing-resource routes return 404. Endpoint control is
only proof of the response over TLS at verification time, not proof of a human's
identity, exclusive ownership or continuing ownership. A generic echo server
cannot express recipient intent; recipients should opt in by implementing this
specific protocol. It is not an OAuth or legal-consent assertion.

## State and time

Challenges have fixed 60-second expiry, owner/exact-endpoint binding, SHA256 nonce
storage and one-time locked consumption. A grant is server-issued, webhook-only,
valid for seven days, and has a revocation timestamp. Neither caller timestamps
nor verifier-method labels are accepted. The two-second per-owner cooldown and
ten challenge rows per live minute limit verification requests. Expired challenge
rows are pruned on the next owner request. Grants are retained for owner review
until account deletion. Owner deletion removes challenges and grants, so recreating
that owner ID does not inherit them. Operator cutover preserves hash/expiry/revoke
state; older snapshots have zero grants rather than guessed proof.

Runtime None clocks are read after authoritative locks. Test-only explicit clocks
and direct store proof setup are not API capabilities. At send intent the worker
locks current check-in identity/preferences and matching grant rows, validates
fresh expiry and quiet hours using one aware runtime instant after every blocking
check-in/profile/grant lock has been acquired, then marks durable delivery started.
Quiet-hour decisions never reuse a timestamp from before a lock wait. Revocation which commits before
intent refuses delivery. Revocation after intent cannot retract a network call.
A granted endpoint changing hands before seven days is not detectable; disable or
revoke immediately when ownership changes. Address changes require new exact proof.

## Network limits

The same existing webhook URL validation checks HTTPS, credentials and public DNS.
The challenge additionally rejects fragments, whitespace/control characters and
backslashes. It does not redirect, use environment proxies, or disable TLS checks.
Its request carries no user message or owner ID. Response budget is 2048 bytes and
HTTP exchange has a ten-second total timeout; DNS validation has a separate
ten-second timeout (the OS lookup thread may finish later, but sends no HTTP). The
existing DNS check/connect rebinding race is NOT fixed by this unit: httpx resolves
again after validation. Deployment still needs an egress policy blocking private,
reserved and metadata addresses, or a separately audited pinned-destination transport.
This unit must not be described as SSRF-proof. A public destination may be contacted
only after an authenticated owner requested verification.

## Rollout

Source landing is not deployment. Stop all old companion workers before applying
PG migration021 and starting new binaries; old workers do not enforce this gate.
Offline SQLite-to-PG cutover requires all writers/workers stopped. Deploy API and
workers together. Existing external check-ins without proof now cancel, including
WhatsApp/iMessage, instead of sending. Users need the receiver flow and seven-day
reverification. Rollback to old workers would remove the fail-closed guarantee.

## Evidence boundary

Tests use isolated SQLite/real PostgreSQL and explicitly marked SSRF localhost TLS
harnesses, not public endpoints. Synthetic store proof setup in adapter-error
regressions proves transport classification only. Test receipts and independent
audit are supplied separately; no production deployment/real recipient is claimed.
