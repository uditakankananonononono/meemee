# MM-PREP-R1: recovery fencing and send boundary

PREP-NORUN. Every new test is authored NOT RUN. This is a proposed contract and
review candidate, not integrated recovery or a passed test report. Base:
a88f6e051de5842b3af5cf5641e06f8af4df08e9; tree
00ed0f508cccea7b1a14cc05851ae32447ae40c4.

## Pre-authoring conflicts and reference status

Corrected M1 reference requested: 7cbad75cba98388b63cd02bbbc39f53fb4c8eed4.
It is REFERENCE ONLY, not a production dependency. Initially absent in fresh
clone and direct fetch ("not our ref"), reported before authoring. The peer then
supplied a narrow new-file format-patch; READ ONLY, never applied or merged.
Actual patch SHA256 and peer-reported SHA256 agree:
ed91756ef670fdd09f74eb72595e3f1d535bc0da2e95e92a0cec723a714f626d.
Attachment label prefix e18ad7e2 is NOT its content checksum. Commit header matches
requested M1, but a commit header alone cannot verify commit object/tree/parent.
Those remain UNAVAILABLE. Manifest pins patch bytes, extracted file bytes and
exact AST source symbol hashes. It includes both reported and actual checksums.
Corrected M1 source checks budget/clocks/interval before active lease denial,
then considers quarantine. Its identifier validation also rejects control chars.
Those semantics match this contract; no reference tests were executed here.

The actual dispatcher is meemee.companion.worker.deliver_due_once, not the
unrelated generic meemee/worker.py. Source hash manifest pins both the dispatcher
and schema/mutators to the stated base. Historical audit results are source
claims only, not runs reproduced by this unit.

## Actual-to-proposed field and signature map

| Meaning | Actual SQLite / PG | Proposed protocol, not implemented |
|---|---|---|
| Check-in identity | id TEXT / text primary key | expected id, must match |
| User owner | user_id TEXT / text NOT NULL | expected user_id + authenticated caller scope |
| Worker identity | ABSENT | lease_owner if needed, distinct from user_id |
| Token | ABSENT | fresh acquisition token, never reused |
| Generation | ABSENT | strictly increasing positive exact-int generation |
| Claim start | ABSENT | claimed_at, trusted finite ordered time |
| Lease expiry/renewal | ABSENT | lease_until, renew via same generation/token CAS |
| Send intent/state | ABSENT | not_started/started/accepted/unknown evidence |
| Receipt binding | ABSENT | generation-bound provider receipt/evidence |
| Budget | attempts INTEGER default 0 / integer; max_attempts default 3 | total started-generation budget, never boolean/fraction |
| Lifecycle | queued/running/done/failed/cancelled CHECK | quarantine representation is undecided |
| Queue time | due_at TEXT / timestamptz | due_at is NOT a lease |
| Generic timestamps | created_at, updated_at TEXT / timestamptz | updated_at is NOT durable send evidence or generation |
| Destination | channel, address | revalidate current owner/channel/address/quiet hours |
| Outcome | message, last_error | last_error text is NOT authoritative send state |

Both store classes expose claim_checkin(self, now: datetime | None = None) ->
dict[str, Any] | None. They select queued work and increment attempts, but return
the pre-claim row. SQLite uses BEGIN IMMEDIATE and queued-state update; PG uses
FOR UPDATE SKIP LOCKED. Neither requires attempts < max_attempts at acquisition.

Both expose finish_checkin(self, checkin_id: str, message: str) -> None;
fail_checkin(self, checkin_id: str, error: str) -> str;
mark_checkin_unknown(self, checkin_id: str, error: str) -> bool;
cancel_claimed_checkin(self, checkin_id: str) -> bool;
finish_local_checkin(self, checkin_id: str, conversation_id: str,
message: str) -> bool. No signature accepts owner/token/generation/lease.
Terminal external mutators primarily predicate id and running status. A running
status alone cannot distinguish reacquired generations. fail_checkin requeues
when attempts < max_attempts, regardless of durable delivery evidence, which is
absent. Unknown currently becomes failed with text, not a distinct quarantine
schema state. This contract requires new signatures/fields; none added here.

## Policy selection, with no mutations

Validate expected identity, token and generation against the authoritative
snapshot. Invalid/empty/padded/oversize identity, token/owner mismatch, invalid
exact-int generation/budget, malformed clock or interval always DENY. Reject
nonfinite, negative and boolean clocks, claimed_at after now, and lease_until <=
claimed_at. Expiry equality is expired. A valid ACTIVE lease DENY for EVERY
state, including started/accepted/unknown. This ordering is intentional and
must be checked against corrected M1 once supplied.

After expiry: started/accepted/unknown or unrecognized evidence NEVER request
retry. Quarantine preserves evidence without external delivery. Positively
not_started plus attempts < max_attempts may REQUEST_RETRY only. Exhausted
budget DENY. Missing/legacy send evidence must map to unknown, never infer
not_started from status, timestamps, channel, error text or missing receipt.
M1 advice does not authorize sends, requeue or consume a retry.

## Proposed atomic transitions

Names below are logical contract fields, NOT existing columns or executable
migration SQL. Every mutation returns affected-row count; zero means refusal.
No caller may send after losing a transition. All compare conditions and writes
must share the same serialized transaction, using authoritative rows, not a
cached snapshot followed by an unconditional UPDATE.

1. Acquisition: queued status + valid remaining budget + current user ownership
   and current permission. Assign fresh token, increment generation and attempts
   exactly once; establish ordered claim/lease times and not_started evidence.
   Return POST-update snapshot. Reject corrupt budget, never exceed total.
2. Renewal: id + user_id + token + generation + running + valid live lease.
   Extend from trusted clock without reducing lease; no generation change.
   A delayed old renewal after takeover changes zero rows.
3. Recovery retry: id + user_id + token + generation + running + same observed
   lease version/timestamp + valid expired lease + not_started + same attempts
   and max_attempts + remaining budget. Atomically queue and invalidate token
   (or directly acquire next generation, peer chooses). Recovery must not
   increment attempts if acquisition owns that count. Two recoverers produce
   ONE retry transition. Subsequent acquisition creates a new generation/token.
4. Expired ambiguity: same identity/version predicates and authoritative expiry
   with started/accepted/unknown -> quarantine (or terminal failed + typed
   evidence, schema decision pending). Exactly one transition. Never queue or
   invoke send; retain intent/receipt for authorized reconciliation.
5. Pre-send boundary: id + user_id + token + generation + running + live lease +
   not_started + valid budget + current permission and destination binding.
   Atomically commit started BEFORE entering adapter.send, no transaction held
   over the network. Only the caller that committed intent may invoke that send
   once. On lost commit acknowledgment, DO NOT send: reconcile, otherwise unknown.
6. Receipt: same generation/token owner fencing, running, started/unknown;
   persist acceptance evidence bound to that acquisition. A receipt is never
   evidence for a different generation. If lease expires before publication,
   do not permit an unfenced finish; reconciliation is separate and cannot send.
7. Finish/fail/cancel/local-publish: require id/user_id/token/generation/running
   and valid live lease. Finish external requires accepted evidence; fail may
   queue only with positively not_started and remaining budget; otherwise
   quarantine. Every old-generation call after reacquisition must change ZERO
   rows and publish ZERO messages/events. Missing/expired fencing refuses.

SQLite plan: serialize with BEGIN IMMEDIATE, conditional update and rowcount,
local inserts/status/events in same transaction. PG plan: lock rows consistently
or use full conditional UPDATE ... RETURNING and rowcount; no read/update gap.
Permission profile row must be synchronized with revoke/update under the same
locking/version scheme, as part of the boundary. Present base has no such
external-send protocol. Peer chooses lock order and deadlock handling.

## Permission and unavoidable external ambiguity

Current worker re-reads profile after generation, checks channel/address and
quiet hours, validates destination, then calls adapter.send. No durable marker
exists before send. Cancellation or unexpected send error marks unknown, and
accepted completion errors attempt terminal unknown fallback. Process death
between these writes is not covered. Explicit ChannelError currently retries;
new protocol must never reset started merely on generic adapter error. Proven
nonacceptance/reconciliation semantics require their own reviewed contract.

Pre-send permission recheck must be inside boundary transaction, not solely a
prior Python check. Revocation can happen AFTER committed intent or during send:
define the permission linearization point and best-effort post-boundary cancel
policy. A database fence cannot recall an external message already sent. Do not
promise absolute no-send-after-revocation without provider participation.
Likewise a paused intent-winning worker can resume after expiry; quarantine
prevents retry, but token fencing alone cannot undo provider effects. No second
worker sends that ambiguous generation. Provider idempotency/reconciliation
could improve this only with verified provider support and owner authority.

## Local transaction is different

Exact built-in LocalChannel + supported store routes to finish_local_checkin.
Base SQLite transaction inserts message, updates conversation and completion
with owner/channel/preference checks. PG locks check-in/conversation and reads
user FOR SHARE before publication in one transaction. Rollback means no local
message; committed publication means done. Existing tests include actual
subprocess death for this local boundary, but NONE were run here. New token/
generation/live-lease CAS must precede the local insertion in that SAME
transaction. A stale worker must not insert even if current row is running.
No durable external started marker is necessary for a same-DB atomic local
publication; no assumption that custom local adapter has that property.

## Authored schedule and mutation acceptance

Model schedules in tests/prep/test_mm_r1_contract.py are sequential simulated
atomic operations only. Two recoverers: outcomes [1,0], no sends. Renewal before
CAS: zero changes. Send intent after stale snapshot: quarantine once, never
queue/send. Changed owner/token/generation or exhausted budget: zero changes.
Old finish/fail after reacquisition: zero changes/new row unchanged. Two send
contenders: one intent/send, loser zero. Revoked permission: zero boundary/send.

Mutation plan, NOT RUN: remove token comparison -> independent-token test must
fail; remove generation comparison -> independent-generation test must fail;
remove owner predicate -> owner variant fails; ignore renewal/expiry -> active
and stale-renewal tests fail; omit/reorder durable intent -> send-contender and
kill-point assertions fail; retry started/accepted/unknown -> quarantine tests
fail; drop budget predicate -> exhausted stale-CAS variant fails. Make each
mutation independently in disposable review copies, never on main. Revert each
and prove green control. Model success does not prove database mutant killing;
repeat predicate removal on the eventual real SQLite and PG implementations.

Kill points: before intent may retry after expiry if positively not_started;
after committed intent before send quarantines even if no provider effect;
provider acceptance before receipt quarantines (unknown); receipt before finish
preserves accepted evidence, quarantines/no resend. No kill test here actually
kills a process. All simulate state/effect count only.

## Verification layers and commands for peer, NOT RUN here

1. Static provenance: test_mm_r1_manifest.py, expected GREEN for base exact
   symbols/files and reference patch/symbol bytes. Mismatches, absent symbols
   and invented mapping FAIL; commit-object identity remains unverified.
2. Policy/model: python -m pytest tests/prep/test_mm_r1_contract.py -q. Expected
   GREEN control, RED for listed contract mutants. 9 functions/36 intended
   cases by static/manual enumeration. No collection/runtime claim.
3. Source gates: python -m pytest tests/prep/test_mm_r1_manifest.py -q. Count
   is 2 functions/16 intended cases, never real transactional verification.
4. Real SQLite transactions: peer must author/bind independent handles, barriers
   at read/CAS/send, trigger aborts, fresh reread, committed rowcount and message
   counts using actual migrated schema. ZERO invented adapter fixture supplied.
5. Real PG transactions: same schedules on independently committed connections,
   locking/isolation and reconnect; no substitute mock/fake SQL proof. Requires
   approved throwaway DB setup; no connection values collected here.
6. External simulation: controlled adapter counts send attempts and acceptance,
   lost receipts and failed post-acceptance writes. Distinct from actual provider.
7. Actual process crash: separate subprocess kill at four defined points, reopen
   SQLite or reconnect PG and inspect durable intent/receipt/lease; never call
   asyncio cancellation a process crash. Existing local crash tests do not
   establish external recovery. Peer supplies future test filenames/commands
   after choosing real API/schema. They are UNAVAILABLE now, not stubbed tests.

No real transaction, external send or process-crash test was authored as if it
were bound to an existing new API. Do not mark acceptance (3)/(4) fully verified
from model schedules. Independent audit and reference commit-object recovery remain required.

## Unresolved migration/auth/lifecycle decisions

Reference commit object/tree/parent; exact schema/API naming; existing running-row backfill (unknown,
not safe retry); authenticated owner vs worker scope; token entropy/retention;
generation lifetime/reset prevention; lease clock authority/renewal; permission
version/lock order; budget consumption timing; quarantine status vs typed failed
state; evidence retention and redaction; supported receipt/idempotency contracts;
manual reconciliation authority; cancellation after committed intent; finite
lease bounds; distributed clock skew; pruning terminal evidence; migration and
rollback strategy. No production/module/migration/endpoint/dependency changes.

## New tests and expected outcomes (all NOT RUN)

- test_active_all_delivery_states_deny_without_mutation: 5 cases, GREEN control;
  RED if active ambiguous rows quarantine or mutate.
- test_expiry_equality_and_ambiguous_never_retry: 4 cases, GREEN; RED if equality
  stays active or ambiguous states retry.
- test_invalid_input_denies_zero_changes: 10 cases, GREEN; RED for unsafe inputs.
- test_two_recoverers_exactly_one_transition_no_send: 1 case, GREEN; RED if
  duplicate transitions or sends occur.
- test_snapshot_stale_before_cas_rechecked: 6 cases, GREEN; RED for stale renewal,
  intent, changed identity/generation or exhausted budget being retryable.
- test_old_worker_cannot_settle_new_generation: 2 cases, GREEN; RED if old
  finish/fail mutates the new row.
- test_independent_fence_predicates_each_required: 3 cases, GREEN; RED for removal
  of token, generation or owner predicate independently.
- test_send_loser_cannot_send_and_permission_revocation_blocks_boundary: 1 case,
  GREEN; RED for two sends, missing intent or disabled permission crossing boundary.
- test_kill_point_model_never_auto_retries_unknown_acceptance: 4 cases, GREEN;
  RED for automatic retries after intent, acceptance or receipt. Simulation only.
- test_exact_base_file_and_symbol_hashes: 15 cases, GREEN; RED for mismatch,
  missing source or invented symbol. Reads base via git, not deployed state.
- test_reference_bytes_and_symbol_hashes_required: 1 case, GREEN for pinned
  reference bytes; RED for missing/mismatched file or symbol. Checks patch header,
  NOT commit-object validity. No helper import or execution.

Total 11 functions, 52 intended cases from AST/manual count, NOT collected/run.
No pytest outcome, mutation kill rate, real crash or backend pass claimed.

Checks run separately: source reads, SHA256 calculation, AST parse/extraction
(no imports/execution), Git metadata and staging. Git whitespace check found
only the preserved reference format-patch signature's trailing space and final
blank line. Those bytes are deliberately unchanged for provenance. No other
lint/build/test/collection/runtime/DB/provider checks were run.
