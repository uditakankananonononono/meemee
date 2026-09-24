from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..schema_registry import register_schema
from ..sensitive import scrub_text
from .models import CheckInPreferences, FactInput, PersonaConfig, UserProfile

SCHEMA = [
    "companion users, persona and check-in preferences",
    "companion facts with fts and supersession",
    "companion conversations and messages",
    "companion check-in durable delivery queue",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CompanionStore:
    """Durable per-user companion state: profiles, facts, conversations and check-ins."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS companion_users (
                user_id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                timezone TEXT NOT NULL,
                persona TEXT NOT NULL,
                checkins TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS companion_facts (
                id INTEGER PRIMARY KEY,
                user_id TEXT NOT NULL,
                category TEXT NOT NULL,
                text TEXT NOT NULL,
                confidence REAL NOT NULL,
                source TEXT NOT NULL,
                superseded_by INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS companion_facts_user ON companion_facts(user_id, id);
            CREATE VIRTUAL TABLE IF NOT EXISTS companion_facts_fts USING fts5(
                text, content='companion_facts', content_rowid='id'
            );
            CREATE TRIGGER IF NOT EXISTS companion_facts_ai AFTER INSERT ON companion_facts BEGIN
                INSERT INTO companion_facts_fts(rowid, text) VALUES (new.id, new.text);
            END;
            CREATE TRIGGER IF NOT EXISTS companion_facts_ad AFTER DELETE ON companion_facts BEGIN
                INSERT INTO companion_facts_fts(companion_facts_fts, rowid, text)
                VALUES('delete', old.id, old.text);
            END;
            CREATE TABLE IF NOT EXISTS companion_conversations (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_message_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS companion_conversations_user
                ON companion_conversations(user_id, channel, last_message_at DESC);
            CREATE TABLE IF NOT EXISTS companion_messages (
                id INTEGER PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS companion_messages_conversation
                ON companion_messages(conversation_id, id);
            CREATE TABLE IF NOT EXISTS companion_message_models (
                message_id INTEGER PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT,
                profile TEXT,
                model TEXT,
                attempts TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS companion_message_models_conversation
                ON companion_message_models(conversation_id, message_id);
            CREATE TABLE IF NOT EXISTS companion_checkins (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                slot TEXT NOT NULL,
                due_at TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK(status IN ('queued','running','done','failed','cancelled')),
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                channel TEXT NOT NULL,
                address TEXT,
                message TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(user_id, slot)
            );
            CREATE INDEX IF NOT EXISTS companion_checkins_due ON companion_checkins(status, due_at);
            CREATE INDEX IF NOT EXISTS companion_checkins_user
                ON companion_checkins(user_id, due_at DESC);
        """)
        register_schema(self.db, "companion", 1, SCHEMA)

    # profiles ---------------------------------------------------------
    def upsert_user(self, profile: UserProfile) -> dict[str, Any]:
        now = _now()
        with self.lock, self.db:
            self.db.execute(
                """INSERT INTO companion_users(user_id,display_name,timezone,persona,checkins,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       display_name=excluded.display_name, timezone=excluded.timezone,
                       persona=excluded.persona, checkins=excluded.checkins, updated_at=excluded.updated_at""",
                (
                    profile.user_id, profile.display_name, profile.timezone,
                    profile.persona.model_dump_json(), profile.checkins.model_dump_json(), now, now,
                ),
            )
        return self.get_user(profile.user_id)  # type: ignore[return-value]

    def _profile(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "user_id": row["user_id"],
            "display_name": row["display_name"],
            "timezone": row["timezone"],
            "persona": json.loads(row["persona"]),
            "checkins": json.loads(row["checkins"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM companion_users WHERE user_id=?", (user_id,)
            ).fetchone()
        return self._profile(row) if row else None

    def profile(self, user_id: str) -> UserProfile | None:
        record = self.get_user(user_id)
        if record is None:
            return None
        return UserProfile(
            user_id=record["user_id"],
            display_name=record["display_name"],
            timezone=record["timezone"],
            persona=PersonaConfig(**record["persona"]),
            checkins=CheckInPreferences(**record["checkins"]),
        )

    def list_users(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM companion_users ORDER BY updated_at DESC, user_id LIMIT ?",
                (min(max(limit, 1), 500),),
            ).fetchall()
        return [self._profile(row) for row in rows]

    # facts ------------------------------------------------------------
    def add_fact(self, user_id: str, fact: FactInput, source: str) -> dict[str, Any]:
        now = _now()
        with self.lock, self.db:
            cursor = self.db.execute(
                """INSERT INTO companion_facts(user_id,category,text,confidence,source,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (user_id, fact.category.strip(), scrub_text(fact.text), fact.confidence, source, now, now),
            )
            ident = int(cursor.lastrowid)
        return self.get_fact(ident)  # type: ignore[return-value]

    def get_fact(self, fact_id: int) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM companion_facts WHERE id=?", (fact_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_facts(self, user_id: str, active_only: bool = True, limit: int = 200) -> list[dict[str, Any]]:
        clause = " AND superseded_by IS NULL" if active_only else ""
        with self.lock:
            rows = self.db.execute(
                f"SELECT * FROM companion_facts WHERE user_id=?{clause} ORDER BY id DESC LIMIT ?",
                (user_id, min(max(limit, 1), 1000)),
            ).fetchall()
        return [dict(row) for row in rows]

    def supersede_fact(self, fact_id: int, replacement_id: int | None = None) -> bool:
        with self.lock, self.db:
            changed = self.db.execute(
                "UPDATE companion_facts SET superseded_by=COALESCE(?, id), updated_at=? WHERE id=? AND superseded_by IS NULL",
                (replacement_id, _now(), fact_id),
            ).rowcount
        return bool(changed)

    def find_fact_text(self, user_id: str, text: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                """SELECT * FROM companion_facts
                   WHERE user_id=? AND text=? AND superseded_by IS NULL LIMIT 1""",
                (user_id, scrub_text(text)),
            ).fetchone()
        return dict(row) if row else None

    def search_facts(self, user_id: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
        safe = " OR ".join(f'"{part}"' for part in query.split() if part) or '""'
        with self.lock:
            rows = self.db.execute(
                """SELECT f.*, bm25(companion_facts_fts) AS score FROM companion_facts_fts
                   JOIN companion_facts f ON f.id = companion_facts_fts.rowid
                   WHERE companion_facts_fts MATCH ? AND f.user_id=? AND f.superseded_by IS NULL
                   ORDER BY score LIMIT ?""",
                (safe, user_id, min(max(limit, 1), 50)),
            ).fetchall()
        return [dict(row) for row in rows]

    # conversations ------------------------------------------------------
    def start_conversation(self, user_id: str, channel: str, conversation_id: str | None = None) -> dict[str, Any]:
        ident = conversation_id or uuid.uuid4().hex
        now = _now()
        with self.lock, self.db:
            self.db.execute(
                """INSERT INTO companion_conversations(id,user_id,channel,created_at,last_message_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(id) DO NOTHING""",
                (ident, user_id, channel, now, now),
            )
        return self.get_conversation(ident)  # type: ignore[return-value]

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM companion_conversations WHERE id=?", (conversation_id,)
            ).fetchone()
        return dict(row) if row else None

    def latest_conversation(self, user_id: str, channel: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute(
                """SELECT * FROM companion_conversations
                   WHERE user_id=? AND channel=? ORDER BY last_message_at DESC, id DESC LIMIT 1""",
                (user_id, channel),
            ).fetchone()
        return dict(row) if row else None

    def list_conversations(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                """SELECT * FROM companion_conversations WHERE user_id=?
                   ORDER BY last_message_at DESC, id DESC LIMIT ?""",
                (user_id, min(max(limit, 1), 200)),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_message(self, conversation_id: str, role: str, content: str) -> dict[str, Any]:
        now = _now()
        with self.lock, self.db:
            cursor = self.db.execute(
                "INSERT INTO companion_messages(conversation_id,role,content,created_at) VALUES(?,?,?,?)",
                (conversation_id, role, scrub_text(content), now),
            )
            self.db.execute(
                "UPDATE companion_conversations SET last_message_at=? WHERE id=?",
                (now, conversation_id),
            )
            ident = int(cursor.lastrowid)
        with self.lock:
            row = self.db.execute(
                "SELECT * FROM companion_messages WHERE id=?", (ident,)
            ).fetchone()
        return dict(row)

    def history(self, conversation_id: str, limit: int = 40) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                """SELECT * FROM (
                       SELECT * FROM companion_messages WHERE conversation_id=?
                       ORDER BY id DESC LIMIT ?
                   ) ORDER BY id ASC""",
                (conversation_id, min(max(limit, 1), 500)),
            ).fetchall()
        return [dict(row) for row in rows]

    # check-ins ----------------------------------------------------------
    def schedule_checkin(
        self,
        user_id: str,
        due_at: datetime,
        slot: str,
        channel: str,
        address: str | None,
        max_attempts: int = 3,
    ) -> tuple[dict[str, Any], bool]:
        ident = uuid.uuid4().hex
        now = _now()
        with self.lock, self.db:
            created = self.db.execute(
                """INSERT INTO companion_checkins(id,user_id,slot,due_at,status,max_attempts,channel,address,created_at,updated_at)
                   VALUES(?,?,?,?,'queued',?,?,?,?,?)
                   ON CONFLICT(user_id, slot) DO NOTHING""",
                (ident, user_id, slot, due_at.astimezone(timezone.utc).isoformat(),
                 max_attempts, channel, address, now, now),
            ).rowcount
            row = self.db.execute(
                "SELECT * FROM companion_checkins WHERE user_id=? AND slot=?", (user_id, slot)
            ).fetchone()
        return dict(row), bool(created)

    def claim_checkin(self, now: datetime | None = None) -> dict[str, Any] | None:
        moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute(
                """SELECT * FROM companion_checkins
                   WHERE status='queued' AND due_at<=? ORDER BY due_at, id LIMIT 1""",
                (moment,),
            ).fetchone()
            if row is None:
                self.db.execute("COMMIT")
                return None
            changed = self.db.execute(
                """UPDATE companion_checkins SET status='running', attempts=attempts+1, updated_at=?
                   WHERE id=? AND status='queued'""",
                (_now(), row["id"]),
            ).rowcount
            self.db.execute("COMMIT")
        return dict(row) if changed else None

    def finish_checkin(self, checkin_id: str, message: str) -> None:
        with self.lock, self.db:
            self.db.execute(
                """UPDATE companion_checkins SET status='done', message=?, last_error=NULL, updated_at=?
                   WHERE id=? AND status='running'""",
                (scrub_text(message), _now(), checkin_id),
            )

    def fail_checkin(self, checkin_id: str, error: str) -> str:
        with self.lock, self.db:
            self.db.execute(
                """UPDATE companion_checkins
                   SET status=CASE WHEN attempts<max_attempts THEN 'queued' ELSE 'failed' END,
                       last_error=?, updated_at=?
                   WHERE id=? AND status='running'""",
                (error[:500], _now(), checkin_id),
            )
            row = self.db.execute(
                "SELECT status FROM companion_checkins WHERE id=?", (checkin_id,)
            ).fetchone()
        return row["status"]

    def cancel_pending_checkins(self, user_id: str) -> int:
        with self.lock, self.db:
            return self.db.execute(
                """UPDATE companion_checkins SET status='cancelled', updated_at=?
                   WHERE user_id=? AND status='queued'""",
                (_now(), user_id),
            ).rowcount

    def list_checkins(
        self, user_id: str, status: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        clauses, parameters = ["user_id=?"], [user_id]
        if status is not None:
            clauses.append("status=?")
            parameters.append(status)
        parameters.append(min(max(limit, 1), 200))
        with self.lock:
            rows = self.db.execute(
                f"SELECT * FROM companion_checkins WHERE {' AND '.join(clauses)} ORDER BY due_at DESC, id DESC LIMIT ?",
                tuple(parameters),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_model_trace(self, message_id: int, conversation_id: str, trace: dict[str, Any]) -> None:
        """Durably record which model profile produced an assistant message."""
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO companion_message_models(message_id,conversation_id,role,profile,model,attempts,created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (message_id, conversation_id, trace.get("role"), trace.get("profile"), trace.get("model"),
                 json.dumps(trace.get("attempts", []), sort_keys=True), _now()),
            )

    def model_traces(self, conversation_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM companion_message_models WHERE conversation_id=? ORDER BY message_id DESC LIMIT ?",
                (conversation_id, limit),
            ).fetchall()
        return [{**dict(r), "attempts": json.loads(r["attempts"])} for r in rows]

    def export_user_data(self, user_id: str) -> dict[str, Any]:
        """Return one user's complete companion state as JSON-safe records."""
        with self.lock:
            user = self.db.execute("SELECT * FROM companion_users WHERE user_id=?", (user_id,)).fetchone()
            facts = self.db.execute("SELECT * FROM companion_facts WHERE user_id=? ORDER BY id", (user_id,)).fetchall()
            conversations = self.db.execute("SELECT * FROM companion_conversations WHERE user_id=? ORDER BY created_at", (user_id,)).fetchall()
            ids = [row["id"] for row in conversations]
            messages = []
            traces = []
            for conversation_id in ids:
                messages.extend(self.db.execute("SELECT * FROM companion_messages WHERE conversation_id=? ORDER BY id", (conversation_id,)).fetchall())
                traces.extend(self.db.execute("SELECT * FROM companion_message_models WHERE conversation_id=? ORDER BY message_id", (conversation_id,)).fetchall())
            checkins = self.db.execute("SELECT * FROM companion_checkins WHERE user_id=? ORDER BY created_at", (user_id,)).fetchall()
        return {"profile": dict(user) if user else None, "facts": [dict(x) for x in facts], "conversations": [dict(x) for x in conversations], "messages": [dict(x) for x in messages], "checkins": [dict(x) for x in checkins], "model_traces": [dict(x) for x in traces]}

    def delete_user_data(self, user_id: str) -> dict[str, int]:
        """Delete customer companion content transactionally; audit lives elsewhere."""
        with self.lock, self.db:
            conversation_ids = [row[0] for row in self.db.execute("SELECT id FROM companion_conversations WHERE user_id=?", (user_id,))]
            messages = 0
            traces = 0
            for conversation_id in conversation_ids:
                messages += self.db.execute("DELETE FROM companion_messages WHERE conversation_id=?", (conversation_id,)).rowcount
                traces += self.db.execute("DELETE FROM companion_message_models WHERE conversation_id=?", (conversation_id,)).rowcount
            counts = {
                "messages": messages,
                "model_traces": traces,
                "conversations": self.db.execute("DELETE FROM companion_conversations WHERE user_id=?", (user_id,)).rowcount,
                "facts": self.db.execute("DELETE FROM companion_facts WHERE user_id=?", (user_id,)).rowcount,
                "checkins": self.db.execute("DELETE FROM companion_checkins WHERE user_id=?", (user_id,)).rowcount,
                "profiles": self.db.execute("DELETE FROM companion_users WHERE user_id=?", (user_id,)).rowcount,
            }
        return counts
