# PostgreSQL persistence path

This package provides PostgreSQL implementations of memory, jobs, plans, API tokens, audit, migrations, rate limits and tool-approval grants. SQLite remains the default. `MEEMEE_PERSISTENCE_BACKEND=postgresql` with `MEEMEE_POSTGRES_DSN` makes the API and `meemee worker` use PostgreSQL for memory, jobs, rate limits and tool-approval grants (other product stores are still per-host SQLite; see STATUS).

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
- Audit appends are globally serialized by an advisory lock and SHA-256 chained. The application role should not own the tables. The migration revokes mutation from `PUBLIC`; explicitly grant only `SELECT, INSERT` on `meemee_audit_log` and sequence usage to the app role.
- Memory search uses a stored `tsvector` and GIN index with `websearch_to_tsquery`.
- Tool-approval grants (`PUT/GET/DELETE /v1/approvals/...`) live in `meemee_tool_approvals` (migration 005). The API and every worker read the same rows, so a grant or revoke made on one host applies to the next tool call of any worker or API process on the database. Expiry is stored as UTC `timestamptz` and is exclusive; non-ISO `expires_at` values are rejected with 422 on both backends. No `approvals.sqlite3` is written in PostgreSQL mode.
- Pool limits are explicit. Set `max_size` below PostgreSQL `max_connections` after reserving room for migrations, maintenance, and replicas.

## Free-tier-first deployment

Use one small PostgreSQL instance, a pool of 2-5 connections per process, and short transactions. Most free tiers cap connections and may suspend idle databases. Run migrations from one release job, not every replica. Monitor connection saturation, transaction age, dead tuples, queue depth, oldest due job, expired leases, and migration checksum failures.

## Cutover from SQLite

1. Stop writers and workers; take and verify a SQLite backup.
2. Apply PostgreSQL migrations.
3. Export SQLite rows in primary-key order. Transform timestamps to UTC `timestamptz`, text JSON to validated `jsonb`, token digests to `bytea`, and SQLite integer IDs to PostgreSQL identity values.
4. Import parent tables before history/events. Reset identity sequences with `setval`.
   `python -m meemee_persist_pg.cli --memory meemee.sqlite3 --plans plans.sqlite3 --jobs jobs.sqlite3 --tokens auth.sqlite3 --audit audit.sqlite3 [--approvals approvals.sqlite3] copy` (DSN from `MEEMEE_POSTGRES_DSN`) does steps 3-5 in one serializable transaction and refuses non-empty targets. Pass `--approvals` to move existing tool grants; without it, grants start empty in PostgreSQL and must be re-granted.
5. Compare row counts and deterministic hashes of canonical exported rows. Verify the full audit chain in both systems.
6. Smoke-test token auth, memory search, plan conflicts, job claim/lease expiry, event cursors, and cancellation.
7. Switch the application configuration once, start one API and one worker, inspect metrics, then scale.
8. Keep SQLite read-only through the rollback window. Rollback means stop all PostgreSQL writers and restore the pre-cutover configuration; do not merge divergent writes.

## Missing, not claimed

- The copy CLI is offline only (stop writers first); it has live PostgreSQL tests for the tool-grant group and the empty-store path, not a production-size rehearsal.
- Logical replication, multi-region failover, online dual-write migration, partitioning, row-level security, and managed backup automation are not claimed.
