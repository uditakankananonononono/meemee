# Supplied monitor event identity

`POST /v1/monitors/evaluate` keeps its caller-supplied fact semantics. The
`runs:write` authenticated principal is the owner. A source/event ID is supplied
by that caller, not proof of connector authorship or a verified external event.

Optional `event_id` selects durable replay handling per owner/source/event ID.
Without it, legacy evaluations may fire repeatedly. With it, one transaction
reserves the canonical payload digest and updates monitor counters/events.
An exact replay returns `status: replay` and no new fires. Changed content on
the same key returns fixed-text HTTP 409; invalid identity/payload returns 422.
No raw exception or payload is included in these errors. Distinct event IDs
with identical payload are distinct events. Identities persist indefinitely;
there is no hidden TTL. Account deletion removes them. Deleting or losing the
ledger permits replay, so it must travel with backups and database cutovers.

SQLite uses BEGIN IMMEDIATE on the monitor database. PostgreSQL migration019
adds the identity table; INSERT ON CONFLICT RETURNING serializes competing
identity reservations, then existing ordered monitor-row locks cover firing.
Reservation and firing roll back together. Replays do not re-evaluate newly
created monitors, deadlines, or changed predicates. Use a new event ID for a
new evaluation, not the same ID to request a different evaluation.

The hash uses the bounded Python sorted JSON encoding from the helper. It is
not RFC8785, Unicode semantic normalization, or numeric equivalence:1 and1.0,
-0.0 and0.0, NFC and NFD have different digests. Caller input is detached before
identity-mode evaluation. No model or external effect is performed by this path.

Portable account export/import now carries digest-only identity rows and rewrites
owner on explicit retargeting. Legacy exports without this optional field have
no identity protection to transfer. Existing exports remain format v1; old
software which ignores this field cannot preserve new replay guarantees. Use
this version on both sides and verify replay after transfer. SQLite multi-file
exports still use separate file reads, not a global snapshot, and the existing
attached-WAL import is not a host-power-loss multi-file atomicity guarantee.
Imports refuse identity collisions rather than silently overwriting them.
Cutover treats a legacy source database without the identity table as empty,
then verifies copied identities with the other monitor tables. Retention does
not delete identities in this first slice.

Evidence is scoped to real ephemeral PostgreSQL and SQLite stores, concurrent
handles, transaction rollback, store reopen/fresh Python process, supplied HTTP
auth cases, transfer and cutover. It does not establish managed/HA PostgreSQL,
authentic connector ingestion, external-source permissions, production rollout,
OS/power-loss recovery or full product readiness.
