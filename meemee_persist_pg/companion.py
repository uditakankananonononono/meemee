"""PostgreSQL companion state: the same contract as ``meemee.companion.store.CompanionStore``.

With per-host companion.sqlite3, a companion user created through one API host did not exist on
another; facts, conversations and message history split by host, and each host's companion worker
only saw its own check-in queue. Here every host reads and writes the same tables. Check-in claims
use ``FOR UPDATE SKIP LOCKED``, so a check-in is sent by one worker even when several run.

Fact search matches the same way as SQLite FTS5 (any query word, no stemming: the ``simple``
configuration); ranking uses ``ts_rank_cd`` instead of bm25, returned as a negative ``score`` so
lower still means more relevant.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from meemee.companion.models import CheckInPreferences, FactInput, PersonaConfig, UserProfile
from meemee.sensitive import scrub_text

from ._db import Database

_TIME_COLUMNS = ("created_at", "updated_at", "last_message_at", "due_at")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = {k: v for k, v in row.items() if k != "fts"}
    for key in _TIME_COLUMNS:
        if isinstance(result.get(key), datetime):
            result[key] = result[key].astimezone(timezone.utc).isoformat()
    return result


class CompanionStore:
    """Durable per-user companion state: profiles, facts, conversations and check-ins (PostgreSQL)."""

    def __init__(self, db: Database):
        self.db = db

    def ping(self) -> bool:
        with self.db.transaction() as c:
            c.execute("SELECT 1 FROM meemee_companion_users LIMIT 0")
        return True

    # profiles ---------------------------------------------------------
    def upsert_user(self, profile: UserProfile) -> dict[str, Any]:
        now = _now()
        with self.db.transaction() as c:
            c.execute("""INSERT INTO meemee_companion_users(user_id,display_name,timezone,persona,checkins,created_at,updated_at)
                         VALUES (%s,%s,%s,%s,%s,%s,%s)
                         ON CONFLICT (user_id) DO UPDATE SET display_name=EXCLUDED.display_name, timezone=EXCLUDED.timezone,
                           persona=EXCLUDED.persona, checkins=EXCLUDED.checkins, updated_at=EXCLUDED.updated_at""",
                      (profile.user_id, profile.display_name, profile.timezone, profile.persona.model_dump_json(),
                       profile.checkins.model_dump_json(), now, now))
        return self.get_user(profile.user_id)  # type: ignore[return-value]

    @staticmethod
    def _profile(row: dict[str, Any]) -> dict[str, Any]:
        record = _row(row)
        return {"user_id": record["user_id"], "display_name": record["display_name"], "timezone": record["timezone"],
                "persona": json.loads(record["persona"]), "checkins": json.loads(record["checkins"]),
                "created_at": record["created_at"], "updated_at": record["updated_at"]}

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self.db.transaction() as c:
            row = c.execute("SELECT * FROM meemee_companion_users WHERE user_id=%s", (user_id,)).fetchone()
        return self._profile(row) if row else None

    def profile(self, user_id: str) -> UserProfile | None:
        record = self.get_user(user_id)
        if record is None:
            return None
        return UserProfile(user_id=record["user_id"], display_name=record["display_name"], timezone=record["timezone"],
                           persona=PersonaConfig(**record["persona"]), checkins=CheckInPreferences(**record["checkins"]))

    def list_users(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.transaction() as c:
            rows = c.execute("SELECT * FROM meemee_companion_users ORDER BY updated_at DESC, user_id LIMIT %s",
                             (min(max(limit, 1), 500),)).fetchall()
        return [self._profile(row) for row in rows]

    # facts ------------------------------------------------------------
    def add_fact(self, user_id: str, fact: FactInput, source: str) -> dict[str, Any]:
        now = _now()
        with self.db.transaction() as c:
            ident = c.execute("""INSERT INTO meemee_companion_facts(user_id,category,text,confidence,source,created_at,updated_at)
                                 VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                              (user_id, fact.category.strip(), scrub_text(fact.text), fact.confidence, source, now, now)).fetchone()["id"]
        return self.get_fact(ident)  # type: ignore[return-value]

    def get_fact(self, fact_id: int) -> dict[str, Any] | None:
        with self.db.transaction() as c:
            return _row(c.execute("SELECT * FROM meemee_companion_facts WHERE id=%s", (fact_id,)).fetchone())

    def list_facts(self, user_id: str, active_only: bool = True, limit: int = 200) -> list[dict[str, Any]]:
        clause = " AND superseded_by IS NULL" if active_only else ""
        with self.db.transaction() as c:
            rows = c.execute(f"SELECT * FROM meemee_companion_facts WHERE user_id=%s{clause} ORDER BY id DESC LIMIT %s",
                             (user_id, min(max(limit, 1), 1000))).fetchall()
        return [_row(row) for row in rows]

    def supersede_fact(self, fact_id: int, replacement_id: int | None = None) -> bool:
        with self.db.transaction() as c:
            return bool(c.execute("""UPDATE meemee_companion_facts SET superseded_by=COALESCE(%s::bigint, id), updated_at=%s
                                     WHERE id=%s AND superseded_by IS NULL""", (replacement_id, _now(), fact_id)).rowcount)

    def find_fact_text(self, user_id: str, text: str) -> dict[str, Any] | None:
        with self.db.transaction() as c:
            return _row(c.execute("""SELECT * FROM meemee_companion_facts WHERE user_id=%s AND text=%s AND superseded_by IS NULL
                                     ORDER BY id LIMIT 1""", (user_id, scrub_text(text))).fetchone())

    def search_facts(self, user_id: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
        terms = [part for part in query.split() if part]
        if not terms:
            return []
        tsquery = " || ".join(["plainto_tsquery('simple', %s)"] * len(terms))
        with self.db.transaction() as c:
            rows = c.execute(f"""WITH q AS (SELECT ({tsquery}) AS query)
                SELECT f.*, -ts_rank_cd(f.fts, q.query) AS score FROM meemee_companion_facts f, q
                WHERE f.fts @@ q.query AND f.user_id=%s AND f.superseded_by IS NULL
                ORDER BY score, f.id LIMIT %s""", (*terms, user_id, min(max(limit, 1), 50))).fetchall()
        return [_row(row) for row in rows]

    # conversations ------------------------------------------------------
    def start_conversation(self, user_id: str, channel: str, conversation_id: str | None = None) -> dict[str, Any]:
        ident, now = conversation_id or uuid.uuid4().hex, _now()
        with self.db.transaction() as c:
            c.execute("""INSERT INTO meemee_companion_conversations(id,user_id,channel,created_at,last_message_at)
                         VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING""", (ident, user_id, channel, now, now))
        return self.get_conversation(ident)  # type: ignore[return-value]

    def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self.db.transaction() as c:
            return _row(c.execute("SELECT * FROM meemee_companion_conversations WHERE id=%s", (conversation_id,)).fetchone())

    def latest_conversation(self, user_id: str, channel: str) -> dict[str, Any] | None:
        with self.db.transaction() as c:
            return _row(c.execute("""SELECT * FROM meemee_companion_conversations WHERE user_id=%s AND channel=%s
                                     ORDER BY last_message_at DESC, id DESC LIMIT 1""", (user_id, channel)).fetchone())

    def list_conversations(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.transaction() as c:
            rows = c.execute("""SELECT * FROM meemee_companion_conversations WHERE user_id=%s
                                ORDER BY last_message_at DESC, id DESC LIMIT %s""", (user_id, min(max(limit, 1), 200))).fetchall()
        return [_row(row) for row in rows]

    def add_message(self, conversation_id: str, role: str, content: str) -> dict[str, Any]:
        now = _now()
        with self.db.transaction() as c:
            row = c.execute("""INSERT INTO meemee_companion_messages(conversation_id,role,content,created_at)
                               VALUES (%s,%s,%s,%s) RETURNING *""", (conversation_id, role, scrub_text(content), now)).fetchone()
            c.execute("UPDATE meemee_companion_conversations SET last_message_at=%s WHERE id=%s", (now, conversation_id))
        return _row(row)  # type: ignore[return-value]

    def history(self, conversation_id: str, limit: int = 40) -> list[dict[str, Any]]:
        with self.db.transaction() as c:
            rows = c.execute("""SELECT * FROM (SELECT * FROM meemee_companion_messages WHERE conversation_id=%s
                                ORDER BY id DESC LIMIT %s) recent ORDER BY id ASC""",
                             (conversation_id, min(max(limit, 1), 500))).fetchall()
        return [_row(row) for row in rows]

    # check-ins ----------------------------------------------------------
    def schedule_checkin(self, user_id: str, due_at: datetime, slot: str, channel: str, address: str | None,
                         max_attempts: int = 3) -> tuple[dict[str, Any], bool]:
        now = _now()
        with self.db.transaction() as c:
            created = c.execute("""INSERT INTO meemee_companion_checkins(id,user_id,slot,due_at,status,max_attempts,channel,address,created_at,updated_at)
                                   VALUES (%s,%s,%s,%s,'queued',%s,%s,%s,%s,%s) ON CONFLICT (user_id, slot) DO NOTHING""",
                                (uuid.uuid4().hex, user_id, slot, due_at.astimezone(timezone.utc), max_attempts, channel, address,
                                 now, now)).rowcount
            row = c.execute("SELECT * FROM meemee_companion_checkins WHERE user_id=%s AND slot=%s", (user_id, slot)).fetchone()
        return _row(row), bool(created)  # type: ignore[return-value]

    def claim_checkin(self, now: datetime | None = None) -> dict[str, Any] | None:
        moment = (now or _now()).astimezone(timezone.utc)
        with self.db.transaction() as c:
            row = c.execute("""SELECT * FROM meemee_companion_checkins WHERE status='queued' AND due_at<=%s
                               ORDER BY due_at, id LIMIT 1 FOR UPDATE SKIP LOCKED""", (moment,)).fetchone()
            if row is None:
                return None
            c.execute("UPDATE meemee_companion_checkins SET status='running', attempts=attempts+1, updated_at=%s WHERE id=%s",
                      (_now(), row["id"]))
        return _row(row)  # the row as it was before the claim, like SQLite

    def finish_checkin(self, checkin_id: str, message: str) -> None:
        with self.db.transaction() as c:
            c.execute("""UPDATE meemee_companion_checkins SET status='done', message=%s, last_error=NULL, updated_at=%s
                         WHERE id=%s AND status='running'""", (scrub_text(message), _now(), checkin_id))

    def fail_checkin(self, checkin_id: str, error: str) -> str:
        with self.db.transaction() as c:
            c.execute("""UPDATE meemee_companion_checkins
                         SET status=CASE WHEN attempts<max_attempts THEN 'queued' ELSE 'failed' END, last_error=%s, updated_at=%s
                         WHERE id=%s AND status='running'""", (error[:500], _now(), checkin_id))
            return c.execute("SELECT status FROM meemee_companion_checkins WHERE id=%s", (checkin_id,)).fetchone()["status"]

    def cancel_pending_checkins(self, user_id: str) -> int:
        with self.db.transaction() as c:
            return c.execute("""UPDATE meemee_companion_checkins SET status='cancelled', updated_at=%s
                                WHERE user_id=%s AND status='queued'""", (_now(), user_id)).rowcount

    def list_checkins(self, user_id: str, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        clauses, parameters = ["user_id=%s"], [user_id]
        if status is not None:
            clauses.append("status=%s"); parameters.append(status)
        parameters.append(min(max(limit, 1), 200))
        with self.db.transaction() as c:
            rows = c.execute(f"SELECT * FROM meemee_companion_checkins WHERE {' AND '.join(clauses)} ORDER BY due_at DESC, id DESC LIMIT %s",
                             tuple(parameters)).fetchall()
        return [_row(row) for row in rows]

    def record_model_trace(self, message_id: int, conversation_id: str, trace: dict[str, Any]) -> None:
        with self.db.transaction() as c:
            c.execute("""INSERT INTO meemee_companion_message_models(message_id,conversation_id,role,profile,model,attempts,created_at)
                         VALUES (%s,%s,%s,%s,%s,%s,%s)
                         ON CONFLICT (message_id) DO UPDATE SET conversation_id=EXCLUDED.conversation_id, role=EXCLUDED.role,
                           profile=EXCLUDED.profile, model=EXCLUDED.model, attempts=EXCLUDED.attempts, created_at=EXCLUDED.created_at""",
                      (message_id, conversation_id, trace.get("role"), trace.get("profile"), trace.get("model"),
                       json.dumps(trace.get("attempts", []), sort_keys=True), _now()))

    def model_traces(self, conversation_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self.db.transaction() as c:
            rows = c.execute("""SELECT * FROM meemee_companion_message_models WHERE conversation_id=%s
                                ORDER BY message_id DESC LIMIT %s""", (conversation_id, limit)).fetchall()
        return [{**_row(r), "attempts": json.loads(r["attempts"])} for r in rows]

    def export_user_data(self, user_id: str) -> dict[str, Any]:
        with self.db.transaction(isolation="REPEATABLE READ") as c:
            user = c.execute("SELECT * FROM meemee_companion_users WHERE user_id=%s", (user_id,)).fetchone()
            facts = c.execute("SELECT * FROM meemee_companion_facts WHERE user_id=%s ORDER BY id", (user_id,)).fetchall()
            conversations = c.execute("SELECT * FROM meemee_companion_conversations WHERE user_id=%s ORDER BY created_at, id",
                                      (user_id,)).fetchall()
            ids = [row["id"] for row in conversations]
            messages = c.execute("""SELECT m.* FROM meemee_companion_messages m JOIN unnest(%s::text[]) WITH ORDINALITY AS o(cid, n)
                                    ON m.conversation_id=o.cid ORDER BY o.n, m.id""", (ids,)).fetchall()
            traces = c.execute("""SELECT t.* FROM meemee_companion_message_models t JOIN unnest(%s::text[]) WITH ORDINALITY AS o(cid, n)
                                  ON t.conversation_id=o.cid ORDER BY o.n, t.message_id""", (ids,)).fetchall()
            checkins = c.execute("SELECT * FROM meemee_companion_checkins WHERE user_id=%s ORDER BY created_at, id", (user_id,)).fetchall()
        return {"profile": _row(user), "facts": [_row(x) for x in facts], "conversations": [_row(x) for x in conversations],
                "messages": [_row(x) for x in messages], "checkins": [_row(x) for x in checkins],
                "model_traces": [_row(x) for x in traces]}

    def delete_user_data(self, user_id: str) -> dict[str, int]:
        owned = "SELECT id FROM meemee_companion_conversations WHERE user_id=%s"
        with self.db.transaction() as c:
            counts = {
                "messages": c.execute(f"DELETE FROM meemee_companion_messages WHERE conversation_id IN ({owned})", (user_id,)).rowcount,
                "model_traces": c.execute(f"DELETE FROM meemee_companion_message_models WHERE conversation_id IN ({owned})", (user_id,)).rowcount,
                "conversations": c.execute("DELETE FROM meemee_companion_conversations WHERE user_id=%s", (user_id,)).rowcount,
                "facts": c.execute("DELETE FROM meemee_companion_facts WHERE user_id=%s", (user_id,)).rowcount,
                "checkins": c.execute("DELETE FROM meemee_companion_checkins WHERE user_id=%s", (user_id,)).rowcount,
                "profiles": c.execute("DELETE FROM meemee_companion_users WHERE user_id=%s", (user_id,)).rowcount,
            }
        return counts
