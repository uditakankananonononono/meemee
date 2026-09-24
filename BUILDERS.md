# Builders

Active build lanes on this repo. One line per lane; update on each push.

| Lane | Scope | Status |
|---|---|---|
| meemee-phaseB-builder (key SHA256:Y6Ie5sNkJ6P4nxWk42f0YHtVBxBSjnItrITESAZelCA) | Model layer: profiles/routing, Inkling (HF + self-hosted), Fugu optional; personal-model reflection routing | 0.118.0 pushed 2026-09-24 |

One line per builder claim. Claim before building; do not edit files another builder has claimed.

- pb4 (branch `pb4`): product-wide account deletion. Files: meemee/account_deletion.py, purge methods on jobs/runs/memory/idempotency/quotas/entitlements/approvals/monitors/personal_model/context/webhooks stores, meemee_persist_pg/jobs.py + memory.py purge, meemee_persist_pg/sql/004_account_purge.sql, worker purge settle, DELETE /v1/account + /v1/admin/principals/{id}/data + /v1/admin/account-deletions/{id}, CLI account-delete / account-delete-resume, tests/test_account_deletion.py, tests_pg/test_account_purge_pg.py. Also one-line fix in meemee_persist_pg/audit.py (UTC verify).

Each parallel builder claims one component here before building, so work does not collide. One line per claim: builder, branch, component, files touched.

- pb7 | branch `pb7` | Forced interruption of blocking tools (process-isolated tool execution, kill on cancel/timeout) | `meemee/isolation.py`, `meemee/tools/base.py` (isolation hook only), `tests/test_isolation.py`, docs

- pb1 | branch `pb1` | Interactive browser human takeover (live sessions, takeover links, WebSocket live view, drag relay, takeover-link notices) | `meemee/browser_sessions.py`, `meemee/browser_api.py`, `meemee/browser_notices.py`, `meemee/tools/browser_session.py`, `tests/test_browser_takeover.py`, `docs/browser-takeover.md` | merged to main in 0.119.0 (PB1 also integrated pb4 + pb7)
