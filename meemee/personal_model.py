from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

PersonalKind = Literal["goal", "relationship", "project", "preference", "routine", "constraint"]


def validity_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("personal validity timestamp requires timezone")
    return parsed.astimezone(timezone.utc)


def currently_valid(row: dict, clock: datetime) -> bool:
    try:
        start = validity_instant(row["valid_from"]) if row.get("valid_from") else None
        end = validity_instant(row["valid_until"]) if row.get("valid_until") else None
        return (start is None or start <= clock) and (end is None or clock < end)
    except (ValueError, TypeError):
        # Legacy malformed validity never becomes trusted active context.
        return False


class PersonalItemInput(BaseModel):
    kind: PersonalKind
    title: str = Field(min_length=1, max_length=240)
    value: str = Field(min_length=1, max_length=10_000)
    confidence: float = Field(default=1.0, ge=0, le=1)
    source_id: str = Field(min_length=1, max_length=240)
    source_record_id: str = Field(min_length=1, max_length=240)
    valid_from: str | None = None
    valid_until: str | None = None

    @field_validator("title", "value", "source_id", "source_record_id")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("personal text and evidence identifiers must not be blank")
        return value

    @field_validator("valid_from", "valid_until")
    @classmethod
    def normalized_time(cls, value):
        return validity_instant(value).isoformat() if value is not None else None

    @model_validator(mode="after")
    def ordered_time(self):
        if self.valid_from and self.valid_until and self.valid_from >= self.valid_until:
            raise ValueError("valid_from must precede valid_until")
        return self


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

    def upsert(self, owner_id: str, item: PersonalItemInput, *, preserve_user: bool = False, expected_current_id: str | None = None) -> dict:
        if not owner_id:
            raise ValueError("owner is required")
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            return self._upsert_locked(owner_id, item, preserve_user, expected_current_id)

    def upsert_batch(self, owner_id: str, items: list[PersonalItemInput], *, preserve_user: bool = False) -> list[dict]:
        if not owner_id:
            raise ValueError("owner is required")
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            return [self._upsert_locked(owner_id, item, preserve_user, None) for item in items]

    def _upsert_locked(self, owner_id, item, preserve_user, expected_current_id):
        now = datetime.now(timezone.utc).isoformat()
        current = self.db.execute(
            "SELECT * FROM personal_items WHERE owner_id=? AND kind=? AND title=? AND status='active'",
            (owner_id, item.kind, item.title.strip()),
        ).fetchone()
        if expected_current_id is not None and (current is None or current["id"] != expected_current_id):
            return {}
        if preserve_user and current:
            confirmed = self.db.execute(
                "SELECT 1 FROM personal_evidence WHERE item_id=? AND owner_id=? AND source_id='user' LIMIT 1",
                (current["id"], owner_id),
            ).fetchone()
            if confirmed:
                return {**dict(current), "reflection_preserved_user": True}
        if current and current["value"] == item.value.strip():
            ident = current["id"]
            combined = max(float(current["confidence"]), item.confidence)
            self.db.execute("UPDATE personal_items SET confidence=?,valid_from=?,valid_until=?,updated_at=? WHERE id=?", (combined, item.valid_from, item.valid_until, now, ident))
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
            if self.db.in_transaction:
                return self._get_locked(owner_id, ident)
            with self.db:
                self.db.execute("BEGIN")
                return self._get_locked(owner_id, ident)

    def _get_locked(self, owner_id: str, ident: str) -> dict | None:
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
        items = [self.get(owner_id, row["id"]) for row in rows]
        if include_history:
            return items
        clock = datetime.now(timezone.utc)
        return [row for row in items if row and currently_valid(row, clock)]

    def correct(self, owner_id: str, ident: str, value: str) -> dict | None:
        current = self.get(owner_id, ident)
        if current is None or current["status"] != "active":
            return None
        return self.upsert(owner_id, PersonalItemInput(
            kind=current["kind"], title=current["title"], value=value, confidence=1.0,
            source_id="user", source_record_id=f"correction:{uuid.uuid4().hex}",
            valid_from=current["valid_from"], valid_until=current["valid_until"],
        ), expected_current_id=ident) or None

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
        clock = validity_instant(at) if at is not None else datetime.now(timezone.utc)
        with self.lock, self.db:
            rows = self.db.execute(
                "SELECT id,valid_until FROM personal_items WHERE owner_id=? AND status='active' AND valid_until IS NOT NULL",
                (owner_id,)).fetchall()
            expired = []
            for row in rows:
                try:
                    if validity_instant(row["valid_until"]) <= clock:
                        expired.append(row["id"])
                except (ValueError, TypeError):
                    expired.append(row["id"])
            self.db.executemany(
                "UPDATE personal_items SET status='superseded',updated_at=? WHERE id=? AND owner_id=? AND status='active'",
                [(clock.isoformat(), ident, owner_id) for ident in expired])
            return len(expired)

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
