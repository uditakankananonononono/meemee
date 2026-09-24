"""Durable queue that tells a companion user a browser takeover link is waiting.

A notice is queued when a takeover is created for a session opened with
``notify_user_id``. Delivery uses the user's configured companion check-in
channel and address (``local`` by default, which stores the message in their
Meemee conversation). The link text is kept only until the notice is delivered
or cancelled, then erased, so takeover tokens do not linger at rest. Notices for
takeovers that already ended are cancelled instead of sent.
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .companion.channels import ChannelError


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TakeoverNoticeQueue:
    def __init__(self, path: Path, max_attempts: int = 3):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.max_attempts = max_attempts
        with self.lock, self.db:
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS browser_takeover_notices (
                    id TEXT PRIMARY KEY, takeover_id TEXT NOT NULL UNIQUE, session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL, text TEXT, status TEXT NOT NULL
                        CHECK(status IN ('queued','delivered','failed','cancelled')),
                    attempts INTEGER NOT NULL DEFAULT 0, channel TEXT, detail TEXT,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS browser_notices_status ON browser_takeover_notices(status, created_at);
            """)

    def enqueue(self, takeover: dict[str, Any], session_id: str, user_id: str) -> str:
        expires = takeover["expires_at"][:16].replace("T", " ")
        text = (f"Meemee needs a hand in the browser: {takeover['reason']}\n"
                f"Take control here (one-time link, expires {expires} UTC): {takeover['url']}")
        notice_id = "bn_" + uuid.uuid4().hex
        now = _now()
        with self.lock, self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO browser_takeover_notices(id, takeover_id, session_id, user_id, text, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                (notice_id, takeover["takeover_id"], session_id, user_id, text, "queued", now, now),
            )
        return notice_id

    def _finish(self, notice_id: str, status: str, detail: str, channel: str | None = None, keep_text: bool = False) -> None:
        with self.lock, self.db:
            self.db.execute(
                f"UPDATE browser_takeover_notices SET status=?, detail=?, channel=COALESCE(?, channel), updated_at=?{'' if keep_text else ', text=NULL'} WHERE id=?",
                (status, detail[:500], channel, _now(), notice_id),
            )

    def list(self, session_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        query = "SELECT id, takeover_id, session_id, user_id, status, attempts, channel, detail, created_at, updated_at FROM browser_takeover_notices"
        params: tuple = ()
        if session_id:
            query += " WHERE session_id=?"
            params = (session_id,)
        with self.lock:
            rows = self.db.execute(query + " ORDER BY created_at DESC LIMIT ?", (*params, max(1, min(limit, 500)))).fetchall()
        return [dict(r) for r in rows]

    async def deliver_pending(self, sessions_store, companion_store, channels: dict, limit: int = 20) -> list[dict[str, Any]]:
        with self.lock:
            rows = [dict(r) for r in self.db.execute(
                "SELECT * FROM browser_takeover_notices WHERE status='queued' ORDER BY created_at LIMIT ?", (limit,)).fetchall()]
        results = []
        for notice in rows:
            takeover = sessions_store.get_takeover(notice["takeover_id"])
            if takeover is None or takeover["finished_at"] is not None or datetime.fromisoformat(takeover["expires_at"]) < datetime.now(timezone.utc):
                self._finish(notice["id"], "cancelled", "takeover already ended")
                results.append({"id": notice["id"], "status": "cancelled"})
                continue
            profile = companion_store.profile(notice["user_id"])
            if profile is None:
                self._finish(notice["id"], "failed", f"unknown companion user: {notice['user_id']}")
                results.append({"id": notice["id"], "status": "failed"})
                continue
            channel_name = profile.checkins.channel
            adapter = channels.get(channel_name)
            try:
                if adapter is None:
                    raise ChannelError(f"unknown companion channel: {channel_name}")
                address = profile.checkins.address
                if not address:
                    if channel_name != "local":
                        raise ChannelError(f"no delivery address for channel {channel_name}")
                    conversation = companion_store.latest_conversation(notice["user_id"], "local") or companion_store.start_conversation(notice["user_id"], "local")
                    address = conversation["id"]
                result = await adapter.send(address, notice["text"])
                self._finish(notice["id"], "delivered", result.detail, channel_name)
                results.append({"id": notice["id"], "status": "delivered", "channel": channel_name})
            except (ChannelError, ValueError, RuntimeError) as exc:
                with self.lock, self.db:
                    self.db.execute("UPDATE browser_takeover_notices SET attempts=attempts+1, detail=?, channel=?, updated_at=? WHERE id=?",
                                    (str(exc)[:500], channel_name, _now(), notice["id"]))
                    attempts = self.db.execute("SELECT attempts FROM browser_takeover_notices WHERE id=?", (notice["id"],)).fetchone()[0]
                if attempts >= self.max_attempts:
                    self._finish(notice["id"], "failed", str(exc))
                results.append({"id": notice["id"], "status": "failed" if attempts >= self.max_attempts else "queued", "error": str(exc)[:200]})
        return results
