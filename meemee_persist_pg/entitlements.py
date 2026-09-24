"""PostgreSQL plan assignments: the same contract as ``meemee.entitlements.EntitlementStore``.

A plan assigned through one host (``PUT /v1/entitlements/{principal}``) is enforced by every host
at the next request; with per-host SQLite it only applied on the host that took the request.
"""
from __future__ import annotations

from meemee.entitlements import PLANS

from ._db import Database


class EntitlementStore:
    """Plan assignment and enforceable product limits (PostgreSQL). Billing is external."""

    def __init__(self, db: Database, default_plan: str = "starter"):
        if default_plan not in PLANS:
            raise ValueError("unknown default plan")
        self.db, self.default_plan = db, default_plan

    def plan_name(self, principal: str) -> str:
        with self.db.transaction() as c:
            row = c.execute("SELECT plan FROM meemee_principal_plans WHERE principal=%s", (principal,)).fetchone()
        return row["plan"] if row else self.default_plan

    def get(self, principal: str) -> dict:
        name = self.plan_name(principal)
        return {"principal": principal, "plan": name, "limits": dict(PLANS[name])}

    def assign(self, principal: str, plan: str, updated_at: str) -> dict:
        if plan not in PLANS:
            raise ValueError(f"unknown plan: {plan}")
        with self.db.transaction() as c:
            c.execute("""INSERT INTO meemee_principal_plans(principal, plan, updated_at) VALUES (%s, %s, %s)
                         ON CONFLICT (principal) DO UPDATE SET plan = excluded.plan, updated_at = excluded.updated_at""",
                      (principal, plan, updated_at))
        return self.get(principal)

    def allows(self, principal: str, resource: str, current: int) -> bool:
        return current < int(PLANS[self.plan_name(principal)][resource])

    def delete_principal(self, principal: str) -> int:
        with self.db.transaction() as c:
            return c.execute("DELETE FROM meemee_principal_plans WHERE principal=%s", (principal,)).rowcount

    def ping(self) -> bool:
        with self.db.transaction() as c:
            c.execute("SELECT 1 FROM meemee_principal_plans LIMIT 0")
        return True
