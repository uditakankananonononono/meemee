# Post-dispatch write failure uncertainty

Registry marks an ordinary caught error after invoking a non-READ tool as
outcome unknown, verify before retrying. No replay, rollback or reconciliation
is added. Validation/signature refusals before invocation keep prior messages.
Known browser owner refusal uses PreDispatchRefusal, before manager action,
retaining the same non-disclosing unknown-session error. This marker is a
trusted implementation contract, not a model-supplied parameter.

Invocation is conservative, not proof an effect happened. Other tools may
validate inside run and receive an uncertainty warning despite no effect.
Cancellation and isolated-tool paths unchanged, not covered by this repair.
Exception text disclosure behavior unchanged. Not exactly-once or device auth.
