# Local monitor workflow

SQLite only. Existing monitor schema is unchanged; opening the store adds intake,
outbox and inbox tables. PostgreSQL has no equivalent workflow yet and the CLI
refuses it rather than silently using local state.

`meemee monitor-event OWNER SOURCE EVENT_ID payload.json` accepts one object.
Fields are top-level monitor predicate fields. Supply a stable event ID; an ID
is immutable within an owner/source. Duplicate IDs are ignored, even if their
payload differs. Intake, evaluation, fire budget and outbox insertion share one
BEGIN IMMEDIATE transaction across connections. `meemee monitor-worker --once`
expires deadlines and dispatches pending triggers into a durable local inbox.
Without --once it repeats every 60 seconds; --interval must be >= 5.
`MonitorStore.notifications(owner)` reads that inbox. This is not external
message delivery and cannot perform an action. Those need separate approval.

A restart resumes pending delivery. Cancellation before dispatch suppresses it,
including one-fire completed monitors; delivered notifications are not recalled.
Timeouts are evaluated without needing a new source event. There is no deadline
notification yet. Owners cannot read each other's inboxes. Local operator CLI
access assumes access to the data directory; it is not a remote authentication
boundary. `delete_owner` removes the new records. Backup includes the SQLite
file. Restore the whole file, not individual tables. No PG cutover/backfill or
inbox UI is provided. Tests use real SQLite connections, competing evaluators,
restart, duplicate input, cancellation, deadline and tenant isolation.
