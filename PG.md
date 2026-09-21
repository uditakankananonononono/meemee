# PostgreSQL persistence path

This additive package provides PostgreSQL implementations of the six persistence surfaces: memory, jobs, plans, API tokens, audit, and migrations. It does not switch the existing runtime automatically. SQLite remains the default until the composition root explicitly selects these stores.

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
- Pool limits are explicit. Set `max_size` below PostgreSQL `max_connections` after reserving room for migrations, maintenance, and replicas.

## Free-tier-first deployment

Use one small PostgreSQL instance, a pool of 2-5 connections per process, and short transactions. Most free tiers cap connections and may suspend idle databases. Run migrations from one release job, not every replica. Monitor connection saturation, transaction age, dead tuples, queue depth, oldest due job, expired leases, and migration checksum failures.

## Cutover from SQLite

1. Stop writers and workers; take and verify a SQLite backup.
2. Apply PostgreSQL migrations.
3. Export SQLite rows in primary-key order. Transform timestamps to UTC `timestamptz`, text JSON to validated `jsonb`, token digests to `bytea`, and SQLite integer IDs to PostgreSQL identity values.
4. Import parent tables before history/events. Reset identity sequences with `setval`.
5. Compare row counts and deterministic hashes of canonical exported rows. Verify the full audit chain in both systems.
6. Smoke-test token auth, memory search, plan conflicts, job claim/lease expiry, event cursors, and cancellation.
7. Switch the application configuration once, start one API and one worker, inspect metrics, then scale.
8. Keep SQLite read-only through the rollback window. Rollback means stop all PostgreSQL writers and restore the pre-cutover configuration; do not merge divergent writes.

## Missing, not claimed

- Core runtime/config wiring is intentionally not included because this package is additive and does not edit core files.
- An automated SQLite-to-PostgreSQL data-copy CLI is not included. The cutover is documented but remains operator-run.
- Logical replication, multi-region failover, online dual-write migration, partitioning, row-level security, and managed backup automation are not claimed.
