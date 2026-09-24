from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

PersonalKind = Literal["goal", "relationship", "project", "preference", "routine", "constraint"]


class PersonalItemInput(BaseModel):
    kind: PersonalKind
    title: str = Field(min_length=1, max_length=240)
    value: str = Field(min_length=1, max_length=10_000)
    confidence: float = Field(default=1.0, ge=0, le=1)
    source_id: str = Field(min_length=1, max_length=240)
    source_record_id: str = Field(min_length=1, max_length=240)
    valid_from: str | None = None
    valid_until: str | None = None


class PersonalModelStore:
    """Owner-scoped personal model whose claims always retain source evidence."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock, self.db:
            self._drop_status_unique()
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA busy_timeout=5000;
                CREATE TABLE IF NOT EXISTS personal_items(
                  id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, kind TEXT NOT NULL,
                  title TEXT NOT NULL, value TEXT NOT NULL, confidence REAL NOT NULL,
                  status TEXT NOT NULL CHECK(status IN ('active','superseded','deleted')),
                  valid_from TEXT, valid_until TEXT, supersedes_id TEXT,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS personal_items_active ON personal_items(owner_id,kind,title) WHERE status='active';
                CREATE INDEX IF NOT EXISTS personal_owner_kind ON personal_items(owner_id,kind,status,updated_at DESC);
                CREATE TABLE IF NOT EXISTS personal_evidence(
                  id INTEGER PRIMARY KEY, item_id TEXT NOT NULL, owner_id TEXT NOT NULL,
                  source_id TEXT NOT NULL, source_record_id TEXT NOT NULL,
                  observed_value TEXT NOT NULL, confidence REAL NOT NULL,
                  observed_at TEXT NOT NULL,
                  UNIQUE(item_id,source_id,source_record_id,observed_value),
                  FOREIGN KEY(item_id) REFERENCES personal_items(id)
                );
                CREATE INDEX IF NOT EXISTS personal_evidence_item ON personal_evidence(owner_id,item_id,observed_at DESC);
            """)

    def _drop_status_unique(self) -> None:
        """Older files declared UNIQUE(owner_id,kind,title,status), which allowed only one superseded or
        deleted row per claim, so a second correction of the same claim failed. Rebuild without it; the
        invariant that matters (one active claim) is a partial unique index."""
        row = self.db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='personal_items'").fetchone()
        if not row or "UNIQUE(owner_id,kind,title,status)" not in row[0]:
            return
        self.db.execute("PRAGMA foreign_keys=OFF")
        self.db.executescript("""
            CREATE TABLE personal_items_new(
              id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, kind TEXT NOT NULL,
              title TEXT NOT NULL, value TEXT NOT NULL, confidence REAL NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('active','superseded','deleted')),
              valid_from TEXT, valid_until TEXT, supersedes_id TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            INSERT INTO personal_items_new SELECT id,owner_id,kind,title,value,confidence,status,valid_from,valid_until,supersedes_id,created_at,updated_at FROM personal_items;
            DROP TABLE personal_items;
            ALTER TABLE personal_items_new RENAME TO personal_items;
        """)

    def ping(self) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1").fetchone() is not None

    def upsert(self, owner_id: str, item: PersonalItemInput) -> dict:
        if not owner_id:
            raise ValueError("owner is required")
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.db:
            current = self.db.execute(
                "SELECT * FROM personal_items WHERE owner_id=? AND kind=? AND title=? AND status='active'",
                (owner_id, item.kind, item.title.strip()),
            ).fetchone()
            if current and current["value"] == item.value.strip():
                ident = current["id"]
                combined = max(float(current["confidence"]), item.confidence)
                self.db.execute("UPDATE personal_items SET confidence=?,updated_at=? WHERE id=?", (combined, now, ident))
            else:
                ident = uuid.uuid4().hex
                previous = current["id"] if current else None
                if previous:
                    self.db.execute("UPDATE personal_items SET status='superseded',updated_at=? WHERE id=?", (now, previous))
                self.db.execute(
                    "INSERT INTO personal_items VALUES(?,?,?,?,?,?,'active',?,?,?,?,?)",
                    (ident, owner_id, item.kind, item.title.strip(), item.value.strip(), item.confidence,
                     item.valid_from, item.valid_until, previous, now, now),
                )
            self.db.execute(
                "INSERT OR IGNORE INTO personal_evidence(item_id,owner_id,source_id,source_record_id,observed_value,confidence,observed_at) VALUES(?,?,?,?,?,?,?)",
                (ident, owner_id, item.source_id, item.source_record_id, item.value.strip(), item.confidence, now),
            )
        return self.get(owner_id, ident) or {}

    def get(self, owner_id: str, ident: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM personal_items WHERE owner_id=? AND id=?", (owner_id, ident)).fetchone()
            evidence = self.db.execute(
                "SELECT source_id,source_record_id,observed_value,confidence,observed_at FROM personal_evidence WHERE owner_id=? AND item_id=? ORDER BY observed_at DESC,id DESC",
                (owner_id, ident),
            ).fetchall()
        if not row:
            return None
        return {**dict(row), "evidence": [dict(item) for item in evidence]}

    def list(self, owner_id: str, kind: PersonalKind | None = None, include_history: bool = False) -> list[dict]:
        clauses, values = ["owner_id=?"], [owner_id]
        if kind:
            clauses.append("kind=?"); values.append(kind)
        if not include_history:
            clauses.append("status='active'")
        with self.lock:
            rows = self.db.execute(
                f"SELECT id FROM personal_items WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC,id DESC", values
            ).fetchall()
        return [self.get(owner_id, row["id"]) for row in rows]

    def correct(self, owner_id: str, ident: str, value: str) -> dict | None:
        current = self.get(owner_id, ident)
        if current is None or current["status"] != "active":
            return None
        return self.upsert(owner_id, PersonalItemInput(
            kind=current["kind"], title=current["title"], value=value, confidence=1.0,
            source_id="user", source_record_id=f"correction:{uuid.uuid4().hex}",
            valid_from=current["valid_from"], valid_until=current["valid_until"],
        ))

    def decay(self, owner_id: str, before: str, factor: float = 0.9) -> int:
        if not 0 <= factor <= 1:
            raise ValueError("decay factor must be between zero and one")
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.db:
            return self.db.execute("""
                UPDATE personal_items SET confidence=confidence*?,updated_at=?
                WHERE owner_id=? AND status='active' AND updated_at<?
                AND NOT EXISTS(SELECT 1 FROM personal_evidence e WHERE e.item_id=personal_items.id AND e.source_id='user')
            """, (factor, now, owner_id, before)).rowcount

    def delete(self, owner_id: str, ident: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.db:
            return bool(self.db.execute(
                "UPDATE personal_items SET status='deleted',updated_at=? WHERE owner_id=? AND id=? AND status!='deleted'",
                (now, owner_id, ident),
            ).rowcount)

    def context(self, owner_id: str) -> str:
        rows = self.list(owner_id)
        return json.dumps([{key: row[key] for key in ("kind", "title", "value", "confidence", "valid_from", "valid_until")} for row in rows])

    def expire(self, owner_id: str, at: str | None = None) -> int:
        clock = at or datetime.now(timezone.utc).isoformat()
        with self.lock, self.db:
            return self.db.execute(
                "UPDATE personal_items SET status='superseded',updated_at=? WHERE owner_id=? AND status='active' AND valid_until IS NOT NULL AND valid_until<=?",
                (clock, owner_id, clock),
            ).rowcount

    def purge_owner(self, owner_id: str) -> dict[str, int]:
        """Hard-delete the owner's whole personal model, including soft-deleted items and evidence.

        ``delete`` is a user-facing soft delete kept for correction history; account deletion
        must not leave that history behind.
        """
        if not owner_id:
            raise ValueError("owner is required")
        with self.lock, self.db:
            evidence = self.db.execute("DELETE FROM personal_evidence WHERE owner_id=?", (owner_id,)).rowcount
            evidence += self.db.execute(
                "DELETE FROM personal_evidence WHERE item_id IN (SELECT id FROM personal_items WHERE owner_id=?)", (owner_id,)
            ).rowcount
            items = self.db.execute("DELETE FROM personal_items WHERE owner_id=?", (owner_id,)).rowcount
        return {"personal_items": items, "personal_evidence": evidence}
