# Builders

Active build lanes on this repo. One line per lane; update on each push.

| Lane | Scope | Status |
|---|---|---|
| meemee-phaseB-builder (key SHA256:Y6Ie5sNkJ6P4nxWk42f0YHtVBxBSjnItrITESAZelCA) | Model layer: profiles/routing, Inkling (HF + self-hosted), Fugu optional; personal-model reflection routing | 0.118.0 pushed 2026-09-24 |
| pb7 (key instinct-pb7-meemee, branch `pb7`) | Forced interruption of blocking tools: `meemee/isolation.py`, `Tool.isolation` hook in `meemee/tools/base.py`, `tests/test_isolation.py` | shipped on pb7 2026-09-24 |
| pb7 (branch `pb7`) | Live PostgreSQL verification: `tests_pg/test_live_stores.py`, `scripts/pg_live_check.py`, fixes in `meemee_persist_pg/jobs.py` (cancel reaping, heartbeat) and `meemee_persist_pg/plans.py` (JSONB). Not touching `audit.py` (pb4 owns the UTC fix) | in progress 2026-09-24 |
