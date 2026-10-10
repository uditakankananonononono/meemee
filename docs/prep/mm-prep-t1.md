# MM-PREP-T1: owner-authenticated initial claim and CAS contract

PREP-NORUN. Every test is authored, NOT RUN. No production change. No repair,
owner-authentication, single-use link or concurrency verdict is claimed.

## Evidence and conflicts before authoring

Base: `a88f6e051de5842b3af5cf5641e06f8af4df08e9`.
Base tree: `00ed0f508cccea7b1a14cc05851ae32447ae40c4`.
Public source: https://github.com/uditakankananonononono/meemee.git
Source symbols, line spans, full-file and symbol SHA256 are in the review manifest.
The corrected reference M3 `b28de877e0955f215b8023d94447bf8c8ff09a11` was absent
locally and public fetch returned `not our ref`. The parent later supplied its
format-patch, read only and never applied or merged. Attachment SHA256 matches
the peer-stated `f365af6f591ba848adbdf02aa7d06a5b1f560a2488aca442905abd4265ef41e7`.
Its parent/tree were not supplied or independently verified. The parent was
notified before authoring of the following base contract conflicts.

- `build_browser_router.takeover_socket` accepts then reads hello ID/token and
  calls `manager.claim(takeover_id, token)` without trusted principal binding.
  It sends exception strings before authentication. Correct token possession is
  not owner authentication. Current hello can coerce malformed fields via str.
- SQLite `BrowserSessionStore.claim_takeover` writes COALESCE and returns None.
  Its lock is per store instance; it is not a one-winner claim result.
- PG `BrowserSessionStore.claim_takeover` likewise COALESCE/None in a transaction.
  No `claimed_at IS NULL` gate, affected-row result or session predicates.
  PG transaction presence alone is not a single-use claim.
- Manager authenticate validates token digest, finish, expiry and live active
  takeover. Expiry uses `<`, so equality is not denied. No owner binding or
  unclaimed predicate. Manager claim calls store then unconditionally changes
  state/event/activity; no winning-result gate.
- HTTP `Authenticator.dependency` validates bearer/bootstrap/OIDC/session via
  HTTP Request and permits admin scope bypass. It does not establish a WS
  binding. `Principal` is a data class, not proof of trustworthy provenance.
  TokenStore maps token owner_id or token id to Principal.id. Neither arbitrary
  constructed Principal nor a client-provided owner string proves identity.
- `release` HTTP endpoint and manager release remain token-based. Input checks
  human state; frame/release/authenticate remain distinct later-action paths.
  No initial-claim helper can safely be substituted as all later-action auth.
- Existing docs explicitly say token-in-first-message. Existing WS fake manager
  tests accept that shape. Existing Chromium E2E connects without owner auth.
  Backend tests cover one claim persistence and finish idempotence, not two
  independent competing initial claims. Cutover verifies records, not CAS.
  Multihost E2E expects owning-host detail on release; generic unauthenticated
  initial failure must not be confused with that existing later-action contract.

These are expected-red requirements, not an instruction to edit existing paths.

## Reference-only comparison

The attached corrected M3 adds helper/test files, not browser wiring. Its
supplied-facts policy selects owner-only initial claim and explicitly says
notification is not delegation. It requires exact fact/bytes types, 32-byte
digests, bounded/control-free identifiers, aware times, no lifecycle markers,
awaiting_human/current/live facts and exclusive expiry. It uses constant-time
digest comparison but trusts the supplied owner string. Trusted authentication,
DB CAS and manager/socket effect gating are still external requirements.
Reason codes are lifecycle-specific internal data; do not expose them preauth.
Its docstring says supplied tests were executed, a reference-source claim only:
no test execution was performed or independently verified by this prep.

The reference is not imported by the authored tests, and its symbols/hashes must
not be treated as symbols present at the base. Oracle tests here model normalized
facts only, not reference exact types, bounds, digest or timestamp implementation.
The strict 256-character/control-free identity boundary is a useful proposed
adapter requirement; peer must bind and verify it with the chosen authenticator.

## Proposed contract, not an invented binding

1. Trusted transport authentication establishes principal and its validity,
   before initial-manager claim/frame/input/release effects. Reject absent,
   revoked, expired, malformed or unverifiable principal. Authenticate with a
   real server-side validator, never requested_by, notify recipient, hello owner
   string or takeover token. Owner-only means principal.id == session.owner_id.
   No automatic admin bypass. Admin and notification delegation remain decisions.
2. Resolve expected session and takeover from current server records. Validate
   ID relationships, normalized 32-byte SHA256 digest with constant-time compare,
   unexpired `now < expires_at`, no claimed_at, finished_at or outcome, live
   awaiting_human session without closed/lost state, current active takeover.
   Missing/malformed inputs fail closed. Internal reason is not public detail.
3. Reserve atomically using current authoritative rows. Exactly one committed
   winner is necessary BEFORE setting manager human state, appending claim event,
   emitting socket claimed or starting frames. A fake helper ALLOW/result is not
   DB CAS evidence. Rollback/error/zero rows/multiple rows never count as success.
4. Reconcile with live owning-host state under a defined lifecycle fence before
   effects. Reservation alone does not serialize Chromium/process state.
5. Return a single generic unauthenticated denial and fixed denial log event.
   Never echo raw token, digest, exception, lifecycle reason, principal input,
   host, owner or resource existence. Do not log hello/request bodies. Close code,
   payload and event shape must not vary by denial cause. Timing uniformity is
   a separate audit, not proved by fixed strings.

### Transport alternatives requiring selection

A. Existing authenticated same-origin browser session, explicitly validated for
WebSocket handshake with origin/CSRF rules, expiry/revocation, scope and owner.
The existing HTTP dependency cannot be assumed to work on WebSocket automatically.

B. Authenticated HTTP owner operation issuing a short-lived server-bound initial
claim ticket, redeemed by WS with explicit audience/session/takeover/owner binding
and one-time redemption. Ticket storage, TTL, replay, origin and invalidation are
new design decisions, not delivered implementations. Avoid token query strings.

C. A deliberately specified WS authentication exchange using a server validator.
Do not put a persistent bearer in URLs/subprotocols/logs or assume browser custom
Authorization headers. Choose actual client capabilities and credential transport
before coding. This document selects none of these and proposes no shared API.

## Transaction verifier designs: peer executes after adapter decisions

No executable integration fixture is fabricated. Tests below describe harness
requirements, order, assertions and mutation controls. The reviewer must bind
actual production APIs and approved local test database resources first. Never
count a skipped fixture or fake affected-row integer as backend coverage.

SQLite: two independent BrowserSessionStore/connection handles to one temporary
DB, not two calls sharing one Python lock. Set current records to owner, active,
awaiting_human, unclaimed, future expiry. Synchronize both callers after stale
snapshot read but before reservation. Both must reread inside transaction.
Expected: one committed claim with affected rows exactly one, one denial with
zero rows, one human transition/event/claimed message/frame-start total.
Assert persisted claimed_at and ownership match winner. Repeat with reversed
scheduling. Do not rely on wall-clock sleep as the barrier. Busy retry may occur
but must not turn a losing caller into an apparent winner.

PG: independent connections/transactions, not the same cursor/pool lease. Same
barrier and assertions. Lock current session/takeover in documented consistent
order or use an equivalently proven single atomic predicate. Confirm transaction
commit, not UPDATE output alone. Handle deadlock/serialization retry by rereading
all conditions; retries cannot replay success effects. Map actual PG schema,
parameter syntax and return semantics independently from SQLite.

Neither current schema exposes a durable active_takeover_id from manager state.
A DB CAS predicate cannot be guessed for that absent field. Peer must decide a
shared active-ID/generation or serialized lifecycle protocol across rotate,
close, finish, claim and host ownership. No migrations authored here.

For each backend, actor A reads valid facts; actor B commits one event below;
then A reserves/rereads. Expected zero winner and no initial effects when B wins:

| Interleaving | Current-state denial and verification |
| --- | --- |
| close | closed marker/state visible; no new human transition |
| finish | finished_at or outcome visible; no claim event |
| rotate | old ID no longer current; no old claimed message/frame |
| other claim | claimed_at visible; loser cannot start stream |
| lost/process ownership change | lost/host generation invalid; no wrong-host frame |
| expiry equality | authoritative now >= expiry; denial even at equal instant |

Also reverse ordering: A commits claim before B lifecycle change. The peer must
specify whether B cancels a pending claimed response/stream, how live generation
is fenced and whether failed live publication consumes the reservation. Do not
silently restore claimed_at after process loss or promise reconnect on old URL.

Side-effect spy records manager state mutation, claim event, socket claimed and
frame start, with sequence/transaction-commit observation. No such spy exists in
production here. The fake test oracle checks ordering expectations only.

### Mutations which the independent verifier must reject

- Remove trusted owner match: correct digest/wrong owner or untrusted owner
  string wins. Matrix must fail, including forged requested_by/notify and admin.
- Remove claimed_at CAS: independent-handle competition has two winners or
  replay starts effects. Snapshot tests alone cannot kill the real DB mutation.
- Remove current takeover match: old ID wins after rotate barrier. Live-host and
  generation-fence test must fail even with matching token/owner.
- Remove exactly-one winner gate: injected zero/multiple/rollback result starts
  any manager/socket/frame effect. Sequence assertion must fail.

Test-oracle mutants in the authored file illustrate all four failures. They do
not mutate production or prove real mutation testing. Peer must capture actual
backend mutant diffs, red result and restoration hashes independently.

## Negative matrix and leakage controls

The JSON fixture lists valid owner and 25 negative cases: correct digest/wrong
owner, forged requested_by/notify, admin nonowner, wrong IDs, equality/past expiry,
finished/outcome, replay, closed/lost/human/not-live, stale/missing current ID,
missing/malformed/wrong digest and missing/malformed/untrusted principal.
All negatives demand no initial manager/socket/frame effects even if a fake
winner integer is injected. Synthetic digest strings are a normalized oracle,
not real digest parsing or constant-time comparison evidence.

Peer transport audit must submit every negative with unique synthetic canaries
in token/digest/owner/requested_by/notify, collect preauth response and logs, and
assert identical generic shape and no canary, detailed lifecycle/host reason or
request body. Include malformed hello/binary/nonobject and validator exceptions.
None of those real WS/log captures are provided here. Source red test detects
current str(exc) leakage only; removing that spelling alone is insufficient.

## Separation and open semantics

Transport identity validation, durable reservation, manager live-host ownership,
active generation and lifecycle fence, later action authority, reconnect and
process-loss recovery are different contracts. Initial denial must not trigger
frame/input/release. An initial helper is not later-action auth. Already-human
state is not a new claim, even if a reconnect may later be permitted. Decide
proof of reconnect identity, session continuity, lease revocation and crash
recovery separately. Admin/notify delegation is not granted by this unit.
No Chromium, real-socket, real DB concurrency or process-crash evidence exists
from authored tests. Independent audit is required before any landing.

## Expected results and verifier commands (NOT RUN)

Peer only, in its selected environment after review, may run:

```
python -m pytest tests/prep/test_mm_prep_t1.py -q
python -m pytest tests/prep/test_mm_prep_t1.py -q -k 'not expected_red'
python -m pytest tests/test_browser_records_backends.py -q
python -m pytest tests/test_takeover_ws_frames.py -q
```

These are verifier commands, NOT execution receipts. Existing regressions alone
are not acceptance. Existing backend suite may select PG/create resources; peer
must choose its own approved environment. Real transaction/mutation/leakage
commands remain UNAVAILABLE pending the chosen implementation and harness.

At the pinned base: source observations and oracle controls expected GREEN;
`expected_red_initial_claim_requires_trusted_principal`, both store CAS cases,
and handshake leakage case expected RED. All tests NOT RUN. After implementation,
replace source-shape checks with concrete auth/CAS/lifecycle/effect integration
verification; do not hide acceptance debt using xfail/skip or accept a renamed
parameter as proof. Source pin observations naturally need separate re-pinning.

Only static source reading, AST syntax/hashing, whitespace review and git artifact
checks were performed for this prep. No test collection, imports, tests, builds,
DBs, migrations, dependency changes or services. Only new docs/prep and tests/prep
files. Reference/base fetch is source retrieval, not runtime network behavior.
