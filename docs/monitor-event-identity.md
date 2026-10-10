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

## Response compatibility and replay outcome

The response now adds `status` for every successful HTTP caller, including
legacy calls without `event_id`: `new`, `replay`, or `legacy`. Existing `fired`
and `source_id` fields remain. Clients requiring exact response keys must
update; this is an additive response-shape compatibility change, not an
unchanged API contract.

Replay always returns `fired: []`. It does NOT return the original fired IDs
or original response. The ledger stores only the identity digest and creation
time, not a recoverable outcome. This protects fire-once execution but does
not provide outcome discovery after a lost response. Use authenticated monitor
state/events to inspect outcomes; do not infer "original event fired nothing"
from a replay response.

## Validation bounds and arbitration failures

Both HTTP modes already limit input to 32,768 bytes of Python default JSON
encoding and 100 top-level event keys. Identified mode additionally requires
exact JSON types, finite numbers, depth at most16 (root at0), at most100 keys
per nested object, at most4096 list entries and4096 total visited nodes
(including keys), integer bit length at most256, and canonical UTF-8 encoding
at most32,768 bytes. Strings have their own32,768 character/byte cap.
Identifiers are exact nonempty strings, at most240 characters/960 UTF-8 bytes,
with no edge whitespace, ASCII controls or DEL. No normalization is applied.
Nested events accepted in legacy mode may therefore receive422 with event_id.
Canonical JSON is decoded to a detached object before identified evaluation;
legacy evaluation does not use that round-trip. Direct store calls do not have
the HTTP preflight limits, but identified calls have the helper limits.

Only identity/helper validation maps to the fixed identity/payload422. Other
lower-layer ValueError/TypeError failures (time parsing, storage implementation)
are not masked as identity errors; absent another handler they remain server
errors. If PostgreSQL arbitration loses the prior identity row to deletion
between the conflict statement and its read, it rolls back and returns fixed
503 `monitor event identity temporarily unavailable`, with Retry-After:1.
It is not a content conflict. Retrying after owner deletion may evaluate anew,
because deletion intentionally removes replay protection. This is not a
concurrent account-deletion lifecycle fence.

SQLite import of identities into an existing legacy monitor database with no
identity table cleanly refuses before account inserts, with an instruction to
open the target through current MonitorStore first. Import does not silently
migrate that target. PostgreSQL ordered locks are the `ORDER BY id FOR UPDATE`
query in `meemee_persist_pg/monitors.py` inside `evaluate_identified`, after
identity reservation; this query was already present before this integration.
