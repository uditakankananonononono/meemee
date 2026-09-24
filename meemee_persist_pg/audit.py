"""PostgreSQL audit chain: one global, tamper-evident chain for every host on the database.

Ordering and scoping:
- There is exactly one chain per database, not one per host or per principal. Every API server,
  worker and CLI on the database appends to it.
- Appends are serialized by a transaction-scoped advisory lock. The transaction runs at READ
  COMMITTED, so the "last entry" read happens after the lock is held and always sees the previous
  append's commit; the identity ``sequence`` is drawn inside the same locked transaction. Chain
  order is therefore sequence order, with no forks under concurrent appends from many hosts.
- ``occurred_at`` is informational; order comes from the lock and ``sequence``, never from clocks,
  so skew between hosts cannot reorder or fork the chain.
- A rolled-back append can leave a gap in ``sequence`` values (identity values are not
  transactional). Links are by hash, so a gap is not a break; deleting or editing any row is.
- ``meemee_audit_chain_base`` holds the link for a chain whose earlier rows were pruned or never
  imported, exactly like the SQLite ``audit_chain_base`` table.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from psycopg.types.json import Jsonb

from ._db import Database

_LOCK = 6758712042962292
ZERO = "0" * 64


class AuditLog:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def hash(previous, occurred, actor, action, resource, outcome, metadata):
        canonical = f"{previous}\x1f{occurred}\x1f{actor}\x1f{action}\x1f{resource}\x1f{outcome}\x1f{metadata}"
        return hashlib.sha256(canonical.encode()).hexdigest()

    @staticmethod
    def _encode(metadata: Any) -> str:
        return json.dumps(metadata or {}, sort_keys=True, separators=(",", ":"))

    def ping(self) -> bool:
        with self.db.transaction() as c:
            return c.execute("SELECT 1 AS ok").fetchone()["ok"] == 1

    def append(self, actor: str, action: str, resource: str, outcome: str, metadata: dict[str, Any] | None = None) -> int:
        encoded = self._encode(metadata)
        # Round-trip through JSON so the stored jsonb re-encodes to exactly the hashed text.
        stored = json.loads(encoded)
        with self.db.transaction(isolation="READ COMMITTED") as c:
            c.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK,))
            row = c.execute("SELECT entry_hash FROM meemee_audit_log ORDER BY sequence DESC LIMIT 1").fetchone()
            if row:
                previous = row["entry_hash"]
            else:
                base = c.execute("SELECT entry_hash FROM meemee_audit_chain_base WHERE singleton=1").fetchone()
                previous = base["entry_hash"] if base else ZERO
            occurred = datetime.now(timezone.utc)
            digest = self.hash(previous, occurred.isoformat(), actor, action, resource, outcome, encoded)
            result = c.execute(
                """INSERT INTO meemee_audit_log(occurred_at,actor_id,action,resource,outcome,metadata,previous_hash,entry_hash)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING sequence""",
                (occurred, actor, action, resource, outcome, Jsonb(stored), previous, digest)).fetchone()
            return int(result["sequence"])

    def verify(self) -> tuple[bool, int | None]:
        with self.db.transaction(isolation="REPEATABLE READ") as c:
            base = c.execute("SELECT sequence,entry_hash FROM meemee_audit_chain_base WHERE singleton=1").fetchone()
            rows = c.execute("SELECT * FROM meemee_audit_log ORDER BY sequence").fetchall()
        previous = base["entry_hash"] if base else ZERO
        floor = int(base["sequence"]) if base else 0
        for row in rows:
            if row["sequence"] <= floor:
                return False, int(row["sequence"])
            occurred = row["occurred_at"].astimezone(timezone.utc).isoformat()
            digest = self.hash(previous, occurred, row["actor_id"], row["action"], row["resource"], row["outcome"],
                               self._encode(row["metadata"]))
            if row["previous_hash"] != previous or row["entry_hash"] != digest:
                return False, int(row["sequence"])
            previous = row["entry_hash"]
        return True, None

    @staticmethod
    def _row(row) -> dict[str, Any]:
        return {**row, "sequence": int(row["sequence"]),
                "occurred_at": row["occurred_at"].astimezone(timezone.utc).isoformat()}

    def list_page(self, after: int = 0, limit: int = 100) -> tuple[list[dict[str, Any]], str | None]:
        page_size = min(max(limit, 1), 500)
        with self.db.transaction() as c:
            rows = c.execute("SELECT * FROM meemee_audit_log WHERE sequence>%s ORDER BY sequence LIMIT %s",
                             (after, page_size + 1)).fetchall()
        items = [self._row(row) for row in rows[:page_size]]
        return items, str(items[-1]["sequence"]) if len(rows) > page_size else None

    def list(self, after: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        return self.list_page(after, limit)[0]
