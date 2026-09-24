# Builders

One line per builder claim. Claim before building; do not edit files another builder has claimed.

- pb4 (branch `pb4`): product-wide account deletion. Files: meemee/account_deletion.py, purge methods on jobs/runs/memory/idempotency/quotas/entitlements/approvals/monitors/personal_model/context/webhooks stores, meemee_persist_pg/jobs.py + memory.py purge, meemee_persist_pg/sql/004_account_purge.sql, worker purge settle, DELETE /v1/account + /v1/admin/principals/{id}/data + /v1/admin/account-deletions/{id}, CLI account-delete / account-delete-resume, tests/test_account_deletion.py, tests_pg/test_account_purge_pg.py. Also one-line fix in meemee_persist_pg/audit.py (UTC verify).
- pb4 (branch `pb4`), second claim: companion console screens. Files: console/assets/views/companion.js, console/assets/api.js (tickCompanionCheckins), console/README.md, tests/test_console_companion_browser.py.
