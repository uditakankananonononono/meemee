"""PostgreSQL account-deletion ledger: the same contract as ``meemee.account_deletion.DeletionLedger``.

With per-host account-deletions.sqlite3 a deletion started on API host A and interrupted was only
resumed when host A restarted, and a synchronous run that host B finished after the owner deleted
their account on host A was kept (B's ledger had no record of the deletion). Here every host sees
the same ledger; a partial unique index keeps one open deletion per principal across hosts.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from ._db import Database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DeletionLedger:
    def __init__(self, db: Database):
        self.db = db

    def ping(self) -> bool:
        with self.db.transaction() as c:
            c.execute("SELECT 1 FROM meemee_account_deletions LIMIT 0")
        return True

    def open(self, principal: str, requested_by: str) -> str:
        with self.db.transaction() as c:
            row = c.execute("""INSERT INTO meemee_account_deletions(id,principal,requested_by,status,started_at,completed_at)
                               VALUES (%s,%s,%s,'in_progress',%s,NULL)
                               ON CONFLICT (principal) WHERE status = 'in_progress' DO NOTHING RETURNING id""",
                            (uuid.uuid4().hex, principal, requested_by, _now())).fetchone()
            if row:
                return row["id"]
            return c.execute("SELECT id FROM meemee_account_deletions WHERE principal=%s AND status='in_progress'",
                             (principal,)).fetchone()["id"]

    def done_steps(self, deletion_id: str) -> dict[str, dict]:
        with self.db.transaction() as c:
            rows = c.execute("SELECT step,counts FROM meemee_account_deletion_steps WHERE deletion_id=%s", (deletion_id,)).fetchall()
        return {row["step"]: json.loads(row["counts"]) for row in rows}

    def record_step(self, deletion_id: str, step: str, counts: dict) -> None:
        with self.db.transaction() as c:
            c.execute("""INSERT INTO meemee_account_deletion_steps(deletion_id,step,counts,finished_at) VALUES (%s,%s,%s,%s)
                         ON CONFLICT (deletion_id,step) DO UPDATE SET counts=EXCLUDED.counts, finished_at=EXCLUDED.finished_at""",
                      (deletion_id, step, json.dumps(counts, sort_keys=True), _now()))

    def complete(self, deletion_id: str) -> None:
        with self.db.transaction() as c:
            c.execute("UPDATE meemee_account_deletions SET status='completed', completed_at=%s WHERE id=%s", (_now(), deletion_id))

    def get(self, deletion_id: str) -> dict | None:
        with self.db.transaction() as c:
            row = c.execute("SELECT * FROM meemee_account_deletions WHERE id=%s", (deletion_id,)).fetchone()
        if not row:
            return None
        return {**dict(row), "steps": self.done_steps(deletion_id)}

    def incomplete(self) -> list[dict]:
        with self.db.transaction() as c:
            rows = c.execute("SELECT * FROM meemee_account_deletions WHERE status='in_progress' ORDER BY started_at").fetchall()
        return [dict(row) for row in rows]

    def deleted_since(self, principal: str, since: str) -> bool:
        with self.db.transaction() as c:
            return c.execute("SELECT 1 FROM meemee_account_deletions WHERE principal=%s AND started_at>=%s LIMIT 1",
                             (principal, since)).fetchone() is not None
