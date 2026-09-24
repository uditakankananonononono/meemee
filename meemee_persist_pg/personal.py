"""PostgreSQL personal model and connected context: the same contracts as
``meemee.personal_model.PersonalModelStore`` and ``meemee.context.ContextStore``.

With per-host personal-model.sqlite3 and context.sqlite3, a claim confirmed or corrected through
one API host did not exist on another, a connector registered on host A was "not registered" on
host B, and the agent and companion on each host assembled different personal context. Here every
host, worker and reflection scheduler reads and writes the same tables.

Context search matches like SQLite FTS5 for plain word queries (every word must appear, no stemming:
the ``simple`` configuration). FTS5 operator syntax (``OR``, ``NEAR``, quotes, ``*``) is not
interpreted; words are matched as plain terms. Ranking uses ``ts_rank_cd`` returned as a negative
``score``, so lower still means more relevant, as with bm25.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from meemee.context import ContextRecord
from meemee.personal_model import PersonalItemInput, PersonalKind

from ._db import Database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PersonalModelStore:
    """Owner-scoped personal model whose claims always retain source evidence (PostgreSQL)."""

    def __init__(self, db: Database):
        self.db = db

    def ping(self) -> bool:
        with self.db.transaction() as c:
            c.execute("SELECT 1 FROM meemee_personal_items LIMIT 0")
        return True

    def upsert(self, owner_id: str, item: PersonalItemInput) -> dict:
        if not owner_id:
            raise ValueError("owner is required")
        now, title, value = _now(), item.title.strip(), item.value.strip()
        with self.db.transaction() as c:
            # Serialize writers of one claim across hosts so two hosts cannot both insert an active row.
            c.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"personal:{owner_id}:{item.kind}:{title}",))
            current = c.execute(
                "SELECT * FROM meemee_personal_items WHERE owner_id=%s AND kind=%s AND title=%s AND status='active'",
                (owner_id, item.kind, title)).fetchone()
            if current and current["value"] == value:
                ident = current["id"]
                c.execute("UPDATE meemee_personal_items SET confidence=%s,updated_at=%s WHERE id=%s",
                          (max(float(current["confidence"]), item.confidence), now, ident))
            else:
                ident = uuid.uuid4().hex
                previous = current["id"] if current else None
                if previous:
                    c.execute("UPDATE meemee_personal_items SET status='superseded',updated_at=%s WHERE id=%s", (now, previous))
                c.execute("""INSERT INTO meemee_personal_items(id,owner_id,kind,title,value,confidence,status,valid_from,valid_until,
                               supersedes_id,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,'active',%s,%s,%s,%s,%s)""",
                          (ident, owner_id, item.kind, title, value, item.confidence, item.valid_from, item.valid_until, previous, now, now))
            c.execute("""INSERT INTO meemee_personal_evidence(item_id,owner_id,source_id,source_record_id,observed_value,confidence,observed_at)
                         VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                      (ident, owner_id, item.source_id, item.source_record_id, value, item.confidence, now))
        return self.get(owner_id, ident) or {}

    def get(self, owner_id: str, ident: str) -> dict | None:
        with self.db.transaction() as c:
            row = c.execute("SELECT * FROM meemee_personal_items WHERE owner_id=%s AND id=%s", (owner_id, ident)).fetchone()
            evidence = c.execute(
                """SELECT source_id,source_record_id,observed_value,confidence,observed_at FROM meemee_personal_evidence
                   WHERE owner_id=%s AND item_id=%s ORDER BY observed_at DESC,id DESC""", (owner_id, ident)).fetchall()
        if not row:
            return None
        return {**dict(row), "evidence": [dict(e) for e in evidence]}

    def list(self, owner_id: str, kind: PersonalKind | None = None, include_history: bool = False) -> list[dict]:
        clauses, values = ["owner_id=%s"], [owner_id]
        if kind:
            clauses.append("kind=%s"); values.append(kind)
        if not include_history:
            clauses.append("status='active'")
        with self.db.transaction() as c:
            rows = c.execute(f"SELECT id FROM meemee_personal_items WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC,id DESC",
                             values).fetchall()
        return [self.get(owner_id, row["id"]) for row in rows]

    def correct(self, owner_id: str, ident: str, value: str) -> dict | None:
        current = self.get(owner_id, ident)
        if current is None or current["status"] != "active":
            return None
        return self.upsert(owner_id, PersonalItemInput(
            kind=current["kind"], title=current["title"], value=value, confidence=1.0,
            source_id="user", source_record_id=f"correction:{uuid.uuid4().hex}",
            valid_from=current["valid_from"], valid_until=current["valid_until"]))

    def decay(self, owner_id: str, before: str, factor: float = 0.9) -> int:
        if not 0 <= factor <= 1:
            raise ValueError("decay factor must be between zero and one")
        with self.db.transaction() as c:
            return c.execute("""UPDATE meemee_personal_items i SET confidence=confidence*%s,updated_at=%s
                WHERE owner_id=%s AND status='active' AND updated_at<%s
                AND NOT EXISTS(SELECT 1 FROM meemee_personal_evidence e WHERE e.item_id=i.id AND e.source_id='user')""",
                             (factor, _now(), owner_id, before)).rowcount

    def delete(self, owner_id: str, ident: str) -> bool:
        with self.db.transaction() as c:
            return bool(c.execute("UPDATE meemee_personal_items SET status='deleted',updated_at=%s WHERE owner_id=%s AND id=%s AND status!='deleted'",
                                  (_now(), owner_id, ident)).rowcount)

    def context(self, owner_id: str) -> str:
        rows = self.list(owner_id)
        return json.dumps([{key: row[key] for key in ("kind", "title", "value", "confidence", "valid_from", "valid_until")} for row in rows])

    def expire(self, owner_id: str, at: str | None = None) -> int:
        clock = at or _now()
        with self.db.transaction() as c:
            return c.execute("""UPDATE meemee_personal_items SET status='superseded',updated_at=%s WHERE owner_id=%s AND status='active'
                                AND valid_until IS NOT NULL AND valid_until<=%s""", (clock, owner_id, clock)).rowcount

    def purge_owner(self, owner_id: str) -> dict[str, int]:
        if not owner_id:
            raise ValueError("owner is required")
        with self.db.transaction() as c:
            evidence = c.execute("DELETE FROM meemee_personal_evidence WHERE owner_id=%s", (owner_id,)).rowcount
            evidence += c.execute("DELETE FROM meemee_personal_evidence WHERE item_id IN (SELECT id FROM meemee_personal_items WHERE owner_id=%s)",
                                  (owner_id,)).rowcount
            items = c.execute("DELETE FROM meemee_personal_items WHERE owner_id=%s", (owner_id,)).rowcount
        return {"personal_items": items, "personal_evidence": evidence}


class ContextStore:
    """Owner-scoped source/event ledger and unified context index (PostgreSQL)."""

    def __init__(self, db: Database):
        self.db = db

    def ping(self) -> bool:
        with self.db.transaction() as c:
            c.execute("SELECT 1 FROM meemee_context_records LIMIT 0")
        return True

    def register_source(self, owner_id: str, source_id: str, connector: str, config: dict, permission: str = "private") -> dict:
        if not owner_id or not source_id or not connector:
            raise ValueError("owner, source and connector are required")
        now = _now()
        with self.db.transaction() as c:
            c.execute("""INSERT INTO meemee_context_sources(owner_id,source_id,connector,config,permission,cursor,created_at,updated_at)
                         VALUES (%s,%s,%s,%s,%s,NULL,%s,%s) ON CONFLICT (owner_id,source_id) DO UPDATE SET connector=EXCLUDED.connector,
                         config=EXCLUDED.config,permission=EXCLUDED.permission,updated_at=EXCLUDED.updated_at""",
                      (owner_id, source_id, connector, json.dumps(config, sort_keys=True), permission, now, now))
        return self.source(owner_id, source_id) or {}

    def source(self, owner_id: str, source_id: str) -> dict | None:
        with self.db.transaction() as c:
            row = c.execute("SELECT * FROM meemee_context_sources WHERE owner_id=%s AND source_id=%s", (owner_id, source_id)).fetchone()
        if not row:
            return None
        result = dict(row); result["config"] = json.loads(result["config"]); return result

    def cursor(self, owner_id: str, source_id: str) -> str | None:
        row = self.source(owner_id, source_id); return row["cursor"] if row else None

    def ingest(self, record: ContextRecord) -> bool:
        if self.source(record.owner_id, record.source_id) is None:
            raise ValueError("source is not registered for owner")
        if record.visibility not in {"private", "agent", "shared"}:
            raise ValueError("invalid visibility")
        digest = hashlib.sha256((record.title + "\0" + record.content).encode()).hexdigest(); now = _now()
        with self.db.transaction() as c:
            inserted = c.execute("""INSERT INTO meemee_context_records(owner_id,source_id,external_id,content_hash,kind,title,content,
                occurred_at,provenance,visibility,cursor,metadata,ingested_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (owner_id,source_id,external_id,content_hash) DO NOTHING""",
                (record.owner_id, record.source_id, record.external_id, digest, record.kind, record.title, record.content,
                 record.occurred_at, json.dumps(record.provenance, sort_keys=True), record.visibility, record.cursor,
                 json.dumps(record.metadata or {}, sort_keys=True), now)).rowcount
            if record.cursor is not None:
                c.execute("UPDATE meemee_context_sources SET cursor=%s,updated_at=%s WHERE owner_id=%s AND source_id=%s",
                          (record.cursor, now, record.owner_id, record.source_id))
        return bool(inserted)

    def search(self, owner_id: str, query: str, limit: int = 12, allowed: set[str] | None = None) -> list[dict]:
        visibility = sorted(allowed or {"private", "agent"})
        if not query.split():
            return []
        with self.db.transaction() as c:
            rows = c.execute("""WITH q AS (SELECT plainto_tsquery('simple', %s) AS query)
                SELECT r.*, -ts_rank_cd(r.fts, q.query) AS score FROM meemee_context_records r, q
                WHERE r.fts @@ q.query AND r.owner_id=%s AND r.visibility = ANY(%s)
                ORDER BY score, r.occurred_at DESC LIMIT %s""",
                             (query, owner_id, visibility, max(1, min(limit, 100)))).fetchall()
        return [self._record(row) for row in rows]

    def owner_watermarks(self) -> dict[str, int]:
        with self.db.transaction() as c:
            rows = c.execute("SELECT owner_id, MAX(id) AS top FROM meemee_context_records GROUP BY owner_id").fetchall()
        return {row["owner_id"]: int(row["top"]) for row in rows}

    def recent(self, owner_id: str, limit: int = 12, allowed: set[str] | None = None) -> list[dict]:
        visibility = sorted(allowed or {"private", "agent"})
        with self.db.transaction() as c:
            rows = c.execute("""SELECT * FROM meemee_context_records WHERE owner_id=%s AND visibility = ANY(%s)
                                ORDER BY occurred_at DESC,id DESC LIMIT %s""", (owner_id, visibility, max(1, min(limit, 100)))).fetchall()
        return [self._record(row) for row in rows]

    @staticmethod
    def _record(row: dict[str, Any]) -> dict:
        result = {k: v for k, v in row.items() if k != "fts"}
        result["provenance"] = json.loads(result["provenance"]); result["metadata"] = json.loads(result["metadata"])
        return result

    def assemble(self, owner_id: str, query: str, limit: int = 12) -> dict:
        records = self.search(owner_id, query, limit)
        if len(records) < limit:
            seen = {row["id"] for row in records}
            records += [row for row in self.recent(owner_id, limit * 2) if row["id"] not in seen][:limit - len(records)]
        return {"owner_id": owner_id, "query": query, "records": records, "sources": sorted({row["source_id"] for row in records})}

    def purge_owner(self, owner_id: str) -> dict[str, int]:
        if not owner_id:
            raise ValueError("owner is required")
        with self.db.transaction() as c:
            records = c.execute("DELETE FROM meemee_context_records WHERE owner_id=%s", (owner_id,)).rowcount
            sources = c.execute("DELETE FROM meemee_context_sources WHERE owner_id=%s", (owner_id,)).rowcount
        return {"context_records": records, "context_sources": sources}
