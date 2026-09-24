# PostgreSQL persistence path

This package provides PostgreSQL implementations of memory, jobs, plans, API tokens, audit, migrations, rate limits and tool-approval grants. SQLite remains the default. `MEEMEE_PERSISTENCE_BACKEND=postgresql` with `MEEMEE_POSTGRES_DSN` makes the API and `meemee worker` use PostgreSQL for memory, jobs, rate limits, tool-approval grants, API tokens, customer accounts, the audit chain and email-verification/password-reset challenges (other product stores are still per-host SQLite; see STATUS).

## Install and initialize

Install `psycopg[binary,pool]>=3.2,<4`. Create a dedicated database and least-privilege application role, require TLS (`sslmode=verify-full` outside a private local network), then run:

```python
from meemee_persist_pg import Database, MigrationStore
db = Database("postgresql://meemee:...@host/meemee?sslmode=verify-full")
print(MigrationStore(db).apply())
```

The migrator takes a transaction-scoped advisory lock, checks every previously applied checksum, rejects unknown versions, and applies pending SQL in one transaction. Never edit an applied migration. Add the next contiguous numbered SQL file. Back up before every upgrade and rehearse restore.

## Operational semantics

- Jobs use `FOR UPDATE SKIP LOCKED`, leases, unique fencing tokens, heartbeats, bounded retries, and automatic expired-lease recovery. Only the worker holding the current unexpired lease can finish/fail/cancel a running job.
- Plan replacement is optimistic (`id + expected_version`) and history is written in the same transaction.
- Token creation stores SHA-256 digests only. Authentication locks the row while recording `last_used_at`; revocation is atomic.
- API tokens and customer accounts (`meemee_api_tokens`, `meemee_accounts`) are shared: a token minted, revoked or rotated on one host, or an account created, locked, reset or disabled on one host, applies on every host at its next request. Session tokens authenticate as their account (`owner_id`). Token `expires_at` must be ISO 8601 on both backends. Timestamps are returned in UTC whatever the server `TimeZone`.
- Audit chain ordering and scope: one global chain per database, shared by every API server, worker and CLI. Appends take a transaction-scoped advisory lock and run at READ COMMITTED, so the previous-hash read happens after the lock and always sees the last committed entry; the identity `sequence` is drawn inside that locked transaction. Chain order is sequence order and never depends on host clocks. A rolled-back append can leave a gap in `sequence` values; links are by hash, so gaps are not breaks, while editing or deleting any row is. `meemee_audit_chain_base` (migration 006) records the link for a chain whose earlier rows were pruned or never imported. (Earlier versions ran appends at SERIALIZABLE, whose snapshot predates the lock; concurrent appends then failed with serialization errors.) Audit anchors and `meemee audit-prune` remain SQLite-only and refuse to run in PostgreSQL mode.
- Audit appends are globally serialized by an advisory lock and SHA-256 chained. The application role should not own the tables. The migration revokes mutation from `PUBLIC`; explicitly grant only `SELECT, INSERT` on `meemee_audit_log` and sequence usage to the app role.
- Memory search uses a stored `tsvector` and GIN index with `websearch_to_tsquery`.
- Email-verification and password-reset challenges live in `meemee_email_verifications` and `meemee_password_resets` (migration 003), so a link issued by one host works on any host. Only SHA-256 digests are stored; consuming a challenge takes a row lock, so the same link clicked on two hosts at once succeeds exactly once. Rows cascade-delete with their account. `MEEMEE_RESEND_API_URL` (default `https://api.resend.com`) exists only so tests can capture outgoing mail.
- Tool-approval grants (`PUT/GET/DELETE /v1/approvals/...`) live in `meemee_tool_approvals` (migration 005). The API and every worker read the same rows, so a grant or revoke made on one host applies to the next tool call of any worker or API process on the database. Expiry is stored as UTC `timestamptz` and is exclusive; non-ISO `expires_at` values are rejected with 422 on both backends. No `approvals.sqlite3` is written in PostgreSQL mode.
- Pool limits are explicit. Set `max_size` below PostgreSQL `max_connections` after reserving room for migrations, maintenance, and replicas.

## Free-tier-first deployment

Use one small PostgreSQL instance, a pool of 2-5 connections per process, and short transactions. Most free tiers cap connections and may suspend idle databases. Run migrations from one release job, not every replica. Monitor connection saturation, transaction age, dead tuples, queue depth, oldest due job, expired leases, and migration checksum failures.

## Cutover from SQLite

1. Stop writers and workers; take and verify a SQLite backup.
2. Apply PostgreSQL migrations.
3. Export SQLite rows in primary-key order. Transform timestamps to UTC `timestamptz`, text JSON to validated `jsonb`, token digests to `bytea`, and SQLite integer IDs to PostgreSQL identity values.
4. Import parent tables before history/events. Reset identity sequences with `setval`.
   `python -m meemee_persist_pg.cli --memory meemee.sqlite3 --plans plans.sqlite3 --jobs jobs.sqlite3 --tokens auth.sqlite3 --audit audit.sqlite3 [--approvals approvals.sqlite3] [--email email-verifications.sqlite3] copy` (DSN from `MEEMEE_POSTGRES_DSN`) does steps 3-5 in one serializable transaction and refuses non-empty targets. Pass `--approvals` to move existing tool grants; without it, grants start empty in PostgreSQL and must be re-granted. Pass `--email` to keep pending verification and reset links working across the cutover; without it, users re-request them. `--tokens` copies API tokens (with owner and kind) and customer accounts (password hashes as-is, so existing passwords and unexpired tokens keep working); `--audit` copies the chain and its chain base, and `verify` plus `GET /v1/audit` confirm it still verifies.
5. Compare row counts and deterministic hashes of canonical exported rows. Verify the full audit chain in both systems.
6. Smoke-test token auth, memory search, plan conflicts, job claim/lease expiry, event cursors, and cancellation.
7. Switch the application configuration once, start one API and one worker, inspect metrics, then scale.
8. Keep SQLite read-only through the rollback window. Rollback means stop all PostgreSQL writers and restore the pre-cutover configuration; do not merge divergent writes.

## Missing, not claimed

- The copy CLI is offline only (stop writers first); it has live PostgreSQL tests for the tool-grant group and the empty-store path, not a production-size rehearsal.
- Logical replication, multi-region failover, online dual-write migration, partitioning, row-level security, and managed backup automation are not claimed.
