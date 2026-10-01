# Local build acceptance, 2026-10-01

Branch: build/local-agency-20261001. Base: 6b8c4442d4f25ba238e78ea9444b9fecdbccaeba.
No remote push. Python 3.12.14, local Linux environment.

Implemented and tested boundaries:
- Monitor intake, predicate evaluation, dedupe, durable outbox and local owner inbox.
- Persistent explicit goal plans, fresh source waits, exact-step approvals and local notes.
- Recurring local RSS/ICS file intake, persistent checkpoints and monitor/goal handoff.
- Paired, signed bounded X11 window observation/title action with replay refusal.
- Windows adapter code exists; actual Windows execution is UNVERIFIED and the adapter refuses to start unless MEEMEE_WINDOWS_ADAPTER_UNVERIFIED_OK=1.
- Local agency stores are included in account export (not import) and removed by account deletion.

These are not unlimited agency, internet-wide awareness, provider delivery, or a
full-computer client. The owner's OS is still unknown: "dell intel core i5" is
hardware. New workflows are SQLite-only, with no PostgreSQL parity. Account
import of the local agency section, UI/HTTP goal routes and native transport
remain missing. Reflection-worker simultaneous-claim races remain Thin (1).
The docs test now checks this honest status instead of pinning Thin (0).

Final exact result lines:

```
544 passed, 114 skipped, 1 warning in 66.14s (0:01:06)
All checks passed!
82 passed, 1 warning in 44.38s
259 passed, 7 skipped in 10.94s
```

Core: pytest -q. Lint: ruff check meemee tests examples/webhook_receiver.
PostgreSQL: scripts/pg_live_check.py with pgserver, real PostgreSQL 16.2.
SDK result is a separate earlier run; it was not rerun after docs-only changes.
Skipped tests are NOT verified features. Core warning concerns Starlette's
httpx TestClient deprecation. Package audit on the rebuilt 0.122.0 wheel:

```json
{"status":"pass","findings":[],"summary":{"findings":0}}
```

Evidence files preserve failing-first and after results for each feature.
The X11 test ran Xvfb and xmessage; the screenshot was visually inspected.
No real-weights model/intelligence-equivalence test or provider delivery test
was run. Existing tests include scripted providers; their success proves only
those stated contracts. None of the four new workflows substitutes a mock
implementation for its production component.

## Literal unresolved lines from the supplied audit

The supplied audit is a record to interpret, not a new authorization source.
None of these whole lines is marked implemented:

- "Make sure meemee is your better version but just like you but better"
  No agreed comparison benchmark; bounded workflow tests cannot prove equivalence
  or superiority.
- "So after meemee is finished and I run it, will it behave exactly as you and can replace you with its intelligence level?"
  This is a question; the answer remains unverified. No model equivalence or
  replacement acceptance evidence exists.
- "Continuous world-awareness"
  Local selected snapshots are implemented, not all-world observation.
- "Native OS-level computer agent"
  Bounded X11 control exists; full OS control and owner-device acceptance do not.
- "Human-like persistent agency loop"
  Explicit finite plans exist; human-like autonomous planning is not proved.
- "Instinct-style "lives with you" product"
  No complete native/background product, real provider delivery or equivalence
  acceptance. Local worker foundations are not a substitute for this claim.

Mac/Wayland adapters, Windows runtime acceptance, live WhatsApp/iMessage delivery,
and real model-weight inference remain unavailable or untested in this build.
See each feature doc for safe operation, revocation, backup and deletion limits.
