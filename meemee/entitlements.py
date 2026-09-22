from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

PLANS = {
    "starter": {"daily_jobs": 100, "webhooks": 3, "persistent_approvals": 10},
    "team": {"daily_jobs": 1_000, "webhooks": 25, "persistent_approvals": 100},
    "business": {"daily_jobs": 10_000, "webhooks": 250, "persistent_approvals": 1_000},
}


class EntitlementStore:
    """Plan assignment and enforceable product limits. Billing is intentionally external."""

    def __init__(self, path: Path, default_plan: str = "starter"):
        if default_plan not in PLANS:
            raise ValueError("unknown default plan")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.default_plan = default_plan
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("CREATE TABLE IF NOT EXISTS principal_plans(principal TEXT PRIMARY KEY, plan TEXT NOT NULL, updated_at TEXT NOT NULL)")

    def plan_name(self, principal: str) -> str:
        row = self.db.execute("SELECT plan FROM principal_plans WHERE principal=?", (principal,)).fetchone()
        return row[0] if row else self.default_plan

    def get(self, principal: str) -> dict:
        name = self.plan_name(principal)
        return {"principal": principal, "plan": name, "limits": dict(PLANS[name])}

    def assign(self, principal: str, plan: str, updated_at: str) -> dict:
        if plan not in PLANS:
            raise ValueError(f"unknown plan: {plan}")
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO principal_plans VALUES(?,?,?) ON CONFLICT(principal) DO UPDATE SET plan=excluded.plan,updated_at=excluded.updated_at",
                (principal, plan, updated_at),
            )
        return self.get(principal)

    def allows(self, principal: str, resource: str, current: int) -> bool:
        return current < int(PLANS[self.plan_name(principal)][resource])


def public_catalog() -> dict:
    return {
        "plans": [{"id": name, "limits": dict(limits)} for name, limits in PLANS.items()],
        "billing": {"status": "external", "note": "No prices or payment processing are claimed by Meemee."},
    }
