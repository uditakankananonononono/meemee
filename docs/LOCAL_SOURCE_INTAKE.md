# Autonomous local source intake

Production service: `meemee intake-worker /path/to/allowed-feeds`, default polling
60 seconds. It only reads owner-selected local files under that resolved root.
Register with `meemee source-register OWNER SOURCE local_rss config.json` or
local_ics. Config: `{"path":"feed.xml","interval_seconds":60,"enabled":true}`.
To revoke, register the same source with enabled false. Network connectors,
absolute paths, parent traversal and escaping symlinks are rejected. No HTTP,
redirects, private IPs, OAuth, credentials, provider charges or DNS are involved.
An external provider setup must be separately authorized and implemented, not
inferred from a file path. This is not awareness of the entire world.

Payload limit 2 MB; snapshot limit 1000 distinct IDs. Polls are persisted and
cadence is clamped to 5..86400 seconds. --once honors due times. A process restart
retains checkpoints and source health. Failed parses keep the last good snapshot;
errors report only exception types. The feed is a complete snapshot: updates
replace previous content and removed IDs delete searchable content and FTS rows.
An empty valid feed removes all records from that source. Don't use partial RSS
pages with this snapshot mode if old records should remain. RSS field parsing
now correctly handles leaf XML elements. ICS parser still has limited timezone
semantics: floating times are UTC, TZID is not resolved. Provider recurrence,
sequence and cancellation reconciliation are not implemented.

Snapshot intents are persisted before writing the context and monitor databases.
An interrupted pending intent replays idempotently; snapshot content hashes and
monitor event IDs prevent duplicate notification effects. Intake calls the real
monitor workflow, dispatches into the local inbox and wakes blocked explicit
waiting goals only when that source snapshot changes. It does not approve notes
or perform external actions. Health remains independently queryable through
SourceHealthStore.status and ContextStore assembles the new facts for agents.

This service is SQLite-only and refuses PostgreSQL. Its tables live in
intake.sqlite3; context, monitors, goals and source health have separate DBs.
There is no atomic multi-file snapshot backup; stop workers before backup/restore.
For owner deletion, use ContextStore.purge_owner, MonitorStore.delete_owner,
GoalStore.delete_owner and delete owner/source rows from intake_snapshots and
source_health/checks. Automatic account export/deletion and PG cutover integration
are not complete. A revoked source retains old records until explicitly purged.
The root directory is a local operator grant boundary, not a remote client API.
