from __future__ import annotations

import uuid
from typing import Any

from ._db import Database


class PlanStore:
    def __init__(self, db: Database): self.db=db
    def create(self, plan: Any) -> dict[str,Any]:
        ident=uuid.uuid4(); document=plan.model_dump(mode="json")
        with self.db.transaction() as c:
            c.execute("INSERT INTO meemee_plans(id,goal,version,document) VALUES(%s,%s,1,%s)",(ident,plan.goal,document))
            c.execute("INSERT INTO meemee_plan_history(plan_id,version,document,reason) VALUES(%s,1,%s,'created')",(ident,document))
        return self.get(str(ident))
    @staticmethod
    def _decode(row):
        if not row: return None
        result=dict(row)
        try:
            from meemee.types import Plan
            result["plan"]=Plan.model_validate(result.pop("document"))
        except ImportError:
            result["plan"]=result.pop("document")
        result["id"]=str(result["id"]); return result
    def get(self, ident: str) -> dict[str,Any]:
        with self.db.transaction() as c: row=c.execute("SELECT * FROM meemee_plans WHERE id=%s",(ident,)).fetchone()
        if not row: raise KeyError(ident)
        return self._decode(row)
    def replace(self, ident: str, plan: Any, expected_version: int, reason: str) -> dict[str,Any]:
        if not reason.strip(): raise ValueError("plan change reason is required")
        doc=plan.model_dump(mode="json")
        with self.db.transaction() as c:
            row=c.execute("""UPDATE meemee_plans SET goal=%s,version=version+1,document=%s,updated_at=clock_timestamp()
              WHERE id=%s AND version=%s RETURNING version""",(plan.goal,doc,ident,expected_version)).fetchone()
            if not row:
                actual=c.execute("SELECT version FROM meemee_plans WHERE id=%s",(ident,)).fetchone()
                if not actual: raise KeyError(ident)
                raise ValueError(f"version conflict: expected {expected_version}, actual {actual['version']}")
            c.execute("INSERT INTO meemee_plan_history(plan_id,version,document,reason) VALUES(%s,%s,%s,%s)",(ident,row["version"],doc,reason))
        return self.get(ident)
    def update_status(self, ident: str, step_id: str, status: str, expected_version: int) -> dict[str,Any]:
        current=self.get(ident); plan=current["plan"]; found=False
        from meemee.types import Plan, PlanStep
        steps=[]
        for step in plan.steps:
            payload=step.model_dump()
            if step.id==step_id: payload["status"],found=status,True
            steps.append(PlanStep.model_validate(payload))
        if not found: raise KeyError(step_id)
        return self.replace(ident,Plan(goal=plan.goal,steps=steps),expected_version,f"status {step_id} -> {status}")
    def history(self, ident: str) -> list[dict[str,Any]]:
        with self.db.transaction() as c: return list(c.execute("SELECT version,reason,created_at,document AS plan FROM meemee_plan_history WHERE plan_id=%s ORDER BY version",(ident,)).fetchall())
