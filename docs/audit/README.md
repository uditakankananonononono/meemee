# README source audit, in progress

Source baseline: main 63e66d6. This is a source audit by the same builder, not an
independent verdict. Independent falsification review is being handled separately.

Original README inventory said 186, but actually contains 187 distinct numbered
items: item 187 occurs before 186. `readme-original-186-claims.csv` preserves 1-186;
the original item 187 remains in the README and must receive an additional verdict.

`readme-audit-progress.csv` currently holds 159 of 187 item-level source-read findings.
The remaining 28 are unreviewed, not real/partial/false by inference. The README has
NOT been replaced by a finished verdict table. Each CSV row preserves original
wording, a pessimistic real/partial/false finding, source function and a one-line
limit. Some rows cite behavior already reproduced in the current full suite; some
are source-read only. Those distinctions are retained in their evidence cells.

No real verdict certifies a production deployment, human-level intelligence, account
connection or every subclaim in a compound item. No model-dependent row is being
marked missing merely because generation weights are unavailable.

New concrete falsifications reproduced while auditing:
- #21: sliding-window claim is false; the implementation uses fixed time buckets.
- #28: failed multi-statement SQLite migration leaves its first created table while
  schema_migrations stays empty; executescript is not transactional here.
- #41: SQLite retention deletes memory but leaves orphan vector rows.
- #60: release-audit demands the old misleading Verified heading, so it rejects the
  honest README with verified_ledger_drift.

Source-read partials needing next work include initial-only browser address checking,
non-enforced PlanStore status transitions, non-bounded synchronous readiness probes,
cross-process SQLite audit-chain append ordering, webhook timestamp/upgrade/header
limits, and deployment/UI/SDK reproduction gaps. These are findings, not permission
to turn them into hidden completion claims. Continue source reads from item 160.
