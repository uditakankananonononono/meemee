"""PostgreSQL browser-session records and takeover notices: the same contracts as
``meemee.browser_sessions.BrowserSessionStore`` and ``meemee.browser_notices.TakeoverNoticeQueue``.

The live Chromium session is process state and stays on the host that opened it. What is shared:
session, takeover and event records (so any host lists them, account deletion on any host erases
them, and a request that reaches the wrong host is told which host holds the session), and the
takeover-notice queue (any host's delivery loop can send a notice; a lease with
``FOR UPDATE SKIP LOCKED`` keeps two hosts from sending the same notice at once). A restarting host
marks only its own sessions lost. Takeover links must still be routed to the owning host.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from meemee.browser_notices import deliver_notices
from meemee.browser_sessions import _digest, _iso

from ._db import Database


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _session(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    record = dict(row); record["allowed_domains"] = json.loads(record["allowed_domains"]); return record


class BrowserSessionStore:
    def __init__(self, db: Database, host_id: str):
        if not host_id:
            raise ValueError("host_id is required")
        self.db, self.host_id = db, host_id

    def ping(self) -> bool:
        with self.db.transaction() as c:
            c.execute("SELECT 1 FROM meemee_browser_sessions LIMIT 0")
        return True

    def delete_owner(self, owner_id: str) -> dict[str, int]:
        with self.db.transaction() as c:
            ids = [r["id"] for r in c.execute("SELECT id FROM meemee_browser_sessions WHERE owner_id=%s", (owner_id,)).fetchall()]
            events = takeovers = 0
            if ids:
                events = c.execute("DELETE FROM meemee_browser_session_events WHERE session_id = ANY(%s)", (ids,)).rowcount
                takeovers = c.execute("DELETE FROM meemee_browser_takeovers WHERE session_id = ANY(%s)", (ids,)).rowcount
            sessions = c.execute("DELETE FROM meemee_browser_sessions WHERE owner_id=%s", (owner_id,)).rowcount
        return {"browser_sessions": sessions, "browser_takeovers": takeovers, "browser_events": events}

    def mark_open_sessions_lost(self) -> int:
        now = _iso(_now())
        with self.db.transaction() as c:
            rows = [r["id"] for r in c.execute("""UPDATE meemee_browser_sessions SET state='lost', closed_at=%s, close_reason='process_restart', updated_at=%s
                WHERE state NOT IN ('closed','lost') AND (host_id=%s OR host_id IS NULL) RETURNING id""", (now, now, self.host_id)).fetchall()]
            if rows:
                c.execute("UPDATE meemee_browser_takeovers SET finished_at=%s, outcome='lost' WHERE finished_at IS NULL AND session_id = ANY(%s)", (now, rows))
        return len(rows)

    def create_session(self, session_id: str, owner_id: str, profile: str | None, allowed_domains: list[str]) -> None:
        now = _iso(_now())
        with self.db.transaction() as c:
            c.execute("""INSERT INTO meemee_browser_sessions(id,owner_id,state,profile,allowed_domains,created_at,updated_at,host_id)
                         VALUES (%s,%s,'agent',%s,%s,%s,%s,%s)""", (session_id, owner_id, profile, json.dumps(allowed_domains), now, now, self.host_id))

    def update_session(self, session_id: str, **values: Any) -> None:
        allowed = {"state", "url", "title", "closed_at", "close_reason"}
        if not values or not set(values) <= allowed:
            raise ValueError("invalid session update")
        values["updated_at"] = _iso(_now())
        assignments = ", ".join(f"{key}=%s" for key in values)
        with self.db.transaction() as c:
            c.execute(f"UPDATE meemee_browser_sessions SET {assignments} WHERE id=%s", (*values.values(), session_id))

    def event(self, session_id: str, actor: str, kind: str, detail: dict[str, Any] | None = None) -> None:
        with self.db.transaction() as c:
            c.execute("INSERT INTO meemee_browser_session_events(session_id,ts,actor,kind,detail) VALUES (%s,%s,%s,%s,%s)",
                      (session_id, _iso(_now()), actor, kind, json.dumps(detail or {}, sort_keys=True)))

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self.db.transaction() as c:
            return _session(c.execute("SELECT * FROM meemee_browser_sessions WHERE id=%s", (session_id,)).fetchone())

    def list_sessions(self, owner_id: str | None, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        with self.db.transaction() as c:
            if owner_id is None:
                rows = c.execute("SELECT * FROM meemee_browser_sessions ORDER BY created_at DESC LIMIT %s", (limit,)).fetchall()
            else:
                rows = c.execute("SELECT * FROM meemee_browser_sessions WHERE owner_id=%s ORDER BY created_at DESC LIMIT %s", (owner_id, limit)).fetchall()
        return [_session(r) for r in rows]

    def events(self, session_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self.db.transaction() as c:
            rows = c.execute("""SELECT id,ts,actor,kind,detail FROM meemee_browser_session_events WHERE session_id=%s
                                ORDER BY id DESC LIMIT %s""", (session_id, max(1, min(limit, 1000)))).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in reversed(rows)]

    def create_takeover(self, takeover_id: str, session_id: str, token: str, reason: str, requested_by: str, expires_at: datetime) -> None:
        with self.db.transaction() as c:
            c.execute("""INSERT INTO meemee_browser_takeovers(id,session_id,token_digest,reason,requested_by,created_at,expires_at)
                         VALUES (%s,%s,%s,%s,%s,%s,%s)""", (takeover_id, session_id, _digest(token), reason, requested_by, _iso(_now()), _iso(expires_at)))

    def get_takeover(self, takeover_id: str) -> dict[str, Any] | None:
        with self.db.transaction() as c:
            row = c.execute("SELECT * FROM meemee_browser_takeovers WHERE id=%s", (takeover_id,)).fetchone()
        if not row:
            return None
        record = dict(row); record["token_digest"] = bytes(record["token_digest"]); return record

    def session_takeovers(self, session_id: str) -> list[dict[str, Any]]:
        with self.db.transaction() as c:
            rows = c.execute("""SELECT id,reason,requested_by,created_at,expires_at,claimed_at,finished_at,outcome,note
                                FROM meemee_browser_takeovers WHERE session_id=%s ORDER BY created_at""", (session_id,)).fetchall()
        return [dict(r) for r in rows]

    def claim_takeover(self, takeover_id: str) -> None:
        with self.db.transaction() as c:
            c.execute("UPDATE meemee_browser_takeovers SET claimed_at=COALESCE(claimed_at,%s) WHERE id=%s", (_iso(_now()), takeover_id))

    def finish_takeover(self, takeover_id: str, outcome: str, note: str | None = None) -> bool:
        with self.db.transaction() as c:
            return c.execute("UPDATE meemee_browser_takeovers SET finished_at=%s, outcome=%s, note=%s WHERE id=%s AND finished_at IS NULL",
                             (_iso(_now()), outcome, note, takeover_id)).rowcount == 1


class TakeoverNoticeQueue:
    def __init__(self, db: Database, max_attempts: int = 3, lease_seconds: float = 60):
        self.db, self.max_attempts, self.lease = db, max_attempts, timedelta(seconds=lease_seconds)

    def ping(self) -> bool:
        with self.db.transaction() as c:
            c.execute("SELECT 1 FROM meemee_browser_takeover_notices LIMIT 0")
        return True

    def enqueue(self, takeover: dict[str, Any], session_id: str, user_id: str) -> str:
        expires = takeover["expires_at"][:16].replace("T", " ")
        text = (f"Meemee needs a hand in the browser: {takeover['reason']}\n"
                f"Take control here (one-time link, expires {expires} UTC): {takeover['url']}")
        notice_id, now = "bn_" + uuid.uuid4().hex, _now().isoformat()
        with self.db.transaction() as c:
            c.execute("""INSERT INTO meemee_browser_takeover_notices(id,takeover_id,session_id,user_id,text,status,created_at,updated_at)
                         VALUES (%s,%s,%s,%s,%s,'queued',%s,%s) ON CONFLICT (takeover_id) DO NOTHING""",
                      (notice_id, takeover["takeover_id"], session_id, user_id, text, now, now))
        return notice_id

    def _finish(self, notice_id: str, status: str, detail: str, channel: str | None = None, keep_text: bool = False) -> None:
        with self.db.transaction() as c:
            c.execute(f"""UPDATE meemee_browser_takeover_notices SET status=%s, detail=%s, channel=COALESCE(%s, channel), updated_at=%s,
                          lease_until=NULL{'' if keep_text else ', text=NULL'} WHERE id=%s""", (status, detail[:500], channel, _now().isoformat(), notice_id))

    def delete_user(self, user_id: str) -> dict[str, int]:
        with self.db.transaction() as c:
            return {"browser_notices": c.execute("DELETE FROM meemee_browser_takeover_notices WHERE user_id=%s", (user_id,)).rowcount}

    def list(self, session_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT id,takeover_id,session_id,user_id,status,attempts,channel,detail,created_at,updated_at FROM meemee_browser_takeover_notices"
        params: tuple = ()
        if session_id:
            query += " WHERE session_id=%s"; params = (session_id,)
        with self.db.transaction() as c:
            rows = c.execute(query + " ORDER BY created_at DESC LIMIT %s", (*params, max(1, min(limit, 500)))).fetchall()
        return [dict(r) for r in rows]

    def _claim(self, limit: int) -> list[dict[str, Any]]:
        now = _now()
        with self.db.transaction() as c:
            rows = c.execute("""UPDATE meemee_browser_takeover_notices SET lease_until=%s WHERE id IN (
                  SELECT id FROM meemee_browser_takeover_notices WHERE status='queued' AND (lease_until IS NULL OR lease_until<%s)
                  ORDER BY created_at LIMIT %s FOR UPDATE SKIP LOCKED) RETURNING *""",
                             ((now + self.lease).isoformat(), now.isoformat(), limit)).fetchall()
        return sorted((dict(r) for r in rows), key=lambda r: r["created_at"])

    def _record_failure(self, notice_id: str, detail: str, channel: str | None) -> int:
        with self.db.transaction() as c:
            return c.execute("""UPDATE meemee_browser_takeover_notices SET attempts=attempts+1, detail=%s, channel=%s, updated_at=%s, lease_until=NULL
                                WHERE id=%s RETURNING attempts""", (detail[:500], channel, _now().isoformat(), notice_id)).fetchone()["attempts"]

    async def deliver_pending(self, sessions_store, companion_store, channels: dict, limit: int = 20) -> list[dict[str, Any]]:
        return await deliver_notices(self, sessions_store, companion_store, channels, limit)
