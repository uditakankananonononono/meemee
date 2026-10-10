# Fenced check-in recovery

This candidate changes the real companion worker, not just recovery advice.
The worker recovers expired claims before acquiring due work. Each acquisition
commits a fresh token, increasing generation, attempt count, aware claim time,
lease expiry and `not_started` evidence. Default lease is300 seconds, renewed
every100 seconds while generation/delivery waits; test/operator override3..3600.
Expiry equality loses ownership. Renewal failure cancels the task and fails loud.
Task cancellation cannot prove that an external provider did not receive a send.

SQLite migrates columns atomically on open; PostgreSQL migration020 adds them.
Legacy running rows have `unknown` evidence and no lease. Recovery makes them
failed/unknown, never queued. This is a deliberate stop on ambiguous old work,
not a delivery result. Expired fenced not_started work retries only with budget
remaining; started/accepted/unknown work becomes failed/unknown. No automatic
resend or hidden reconciliation of such rows. Attempts count acquisitions.

Token/user/generation/status/lease gate each worker mutation in a transaction.
Old ID-only APIs remain for legacy callers but cannot change fenced rows; legacy
claim cannot acquire previously fenced rows. Updated worker uses only fenced APIs.
This changes internal extension seams: publication/error injection adapters must
use finish_checkin_claim/unknown_checkin_claim and pass claim to local completion.
Public views/export exclude claim_token; operator cutover preserves it. New
status details and generation/timestamp/delivery metadata are additive user-view
fields. There is no new recovery HTTP endpoint exposing private recovery reasons.

External send intent is committed before adapter.send. Preferences/owner/channel/
address/quiet-hours are checked at that commit. Revocation after intent may race
an already started send, and database fencing cannot revoke provider-side bytes.
The existing ChannelError definite-failure contract permits retry; arbitrary
exceptions/cancellation/DeliveryOutcomeUnknown do not. Custom adapters must honor
that contract. A ChannelError after an accepted side effect is unsafe; this slice
does not authenticate custom adapter correctness or add provider idempotency keys.
Local built-in delivery instead commits message plus completion atomically and
rechecks current permissions and claim inside the transaction.

Client SIGKILL tests on actual SQLite and PostgreSQL distinguish before-intent
recoverable work from after-intent unknown work. They do not kill PostgreSQL or
prove host-power-loss durability. Both clocks must share an aware trusted time
domain; caller-controlled test clock is not a public request field. Database/host
clock jumps, event-loop starvation, renewal beyond active provider calls, live
account deletion and rolling old binaries remain operational risks. SQLite v2
schema rejects old registered binaries; old PG workers require drain/stop before
rollout. Offline cutover requires all workers stopped. It preserves all lease
columns and marks missing legacy evidence unknown. It is not a live migration.

No actual provider send, new channel permission, destination attestation, deployed
account inventory, Claire write-tool integration, selected-model reasoning,
production readiness or whole-root-green result follows from this candidate.
R1 source-pinned prep remains historical atc779375; its assertions do not become
DB concurrency evidence. This implementation awaits independent audit/reproduction.

## Lock-wait time boundary

Runtime ownership locks the current generation row without a precomputed lease
predicate, then reads a fresh aware clock and requires claimed_at <= now < expiry.
Intent/local completion check again after permission/conversation row-lock waits.
Renewal cannot revive an expired generation. Acquisition timestamps are assigned
after its row/table/transaction waits. An explicit now argument is a deliberate
fixed test/operator clock, not a runtime cached timestamp; callers must not pass
an earlier runtime instant to avoid expiry. Tests hold real SQLite write locks or
PG row/table locks past expiry. No clock check makes the following CPU instruction
and database commit physically simultaneous; provider effects remain nontransactional.
