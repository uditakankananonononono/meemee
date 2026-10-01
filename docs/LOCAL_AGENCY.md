# Local persistent agency

The production consumer is `meemee agency-worker OWNER` (60-second cadence).
Use --once for an operator tick. Requires SQLite, not PostgreSQL. Create with
`meemee goal-create OWNER DESCRIPTION plan.json`. The JSON is an object:

```json
{"steps":[{"kind":"wait","source_id":"feed","contains":"ready","max_age_seconds":600},{"kind":"note","text":"Ready recorded"},{"kind":"note","text":"Follow-up complete"}]}
```

This is an explicit finite plan, not a model planner and not unlimited agency.
A wait examines at most the 100 most recent private/agent records, checks exact
source, provenance and timestamp age. Future/stale evidence cannot proceed.
Waits become blocked rather than busy-looping. Source intake wakes blocked
waiting goals when a snapshot changes; a trusted local operator can also call
GoalStore.wake. A note requires exact-step approval with
`meemee goal-approve OWNER GOAL_ID STEP` after the owner inspects its text.
Grants hash the whole action, and revocation is checked at execution time.
Only local durable notes are supported. No shell commands, network actions,
external messages, free-form agent runs or paid models are silently substituted.

The existing dependency/priority/lease GoalStore is consumed, not duplicated.
Two workers share leases. Execution rechecks the live lease. Note effect,
progress, event and goal acknowledgement commit in one SQLite transaction;
a crash rolls all back or preserves all, so replay cannot duplicate notes.
Follow-up steps proceed on later ticks, then the goal stops. Unsupported plans
fail visibly. Cancel through GoalStore.transition; correcting a blocked goal
requires cancellation and a new plan. No model outage can generate fallback
success because this loop invokes no model. This is a bounded foundation, not
proof of the human-like or better-than-you claims.

Schema is additive on goals.sqlite3. Backup/restore the whole DB with context;
GoalStore.delete_owner deletes graph, grants, events, progress and local notes.
Account deletion (`meemee account-delete`, API) removes goals and all execution tables, and `account-export` includes them; import does not restore them. PostgreSQL migration parity, HTTP goal
routes and a goal UI remain missing. Tests prove restart, fresh observation,
approval, effects, follow-up, dependency, competition, revocation and cancellation.
