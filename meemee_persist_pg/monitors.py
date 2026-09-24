"""PostgreSQL monitors and reflection schedule: the same contracts as
``meemee.monitors.MonitorStore`` and ``meemee.reflection_schedule.ReflectionSchedule``.

With per-host monitors.sqlite3 a monitor created through API host A was a 404 on host B and could
not be cancelled there; with per-host reflection-schedule.sqlite3 each reflection worker kept its own
watermarks, so every worker re-reflected every owner. Here all hosts share the tables. ``evaluate``
locks the owner's active monitors (``FOR UPDATE``), so two hosts evaluating the same event cannot both
count a fire past ``max_fires``.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from meemee.monitors import MonitorInput
from meemee.monitors import MonitorStore as _SQLiteMonitors

from ._db import Database


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MonitorStore:
    matches = staticmethod(_SQLiteMonitors.matches)

    def __init__(self, db: Database):
        self.db = db

    def ping(self) -> bool:
        with self.db.transaction() as c:
            c.execute("SELECT 1 FROM meemee_monitors LIMIT 0")
        return True

    @staticmethod
    def _event(c, ident, owner, kind, payload):
        c.execute("INSERT INTO meemee_monitor_events(monitor_id,owner_id,kind,payload,created_at) VALUES (%s,%s,%s,%s,%s)",
                  (ident, owner, kind, json.dumps(payload, sort_keys=True), _now().isoformat()))

    def create(self, owner_id: str, item: MonitorInput) -> dict:
        if not owner_id:
            raise ValueError("owner is required")
        ident, now = uuid.uuid4().hex, _now().isoformat()
        predicate = {"field": item.field, "operator": item.operator, "expected": item.expected}
        with self.db.transaction() as c:
            c.execute("""INSERT INTO meemee_monitors(id,owner_id,name,source_id,predicate,deadline,max_fires,fire_count,status,created_at,updated_at)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,0,'active',%s,%s)""",
                      (ident, owner_id, item.name, item.source_id, json.dumps(predicate, sort_keys=True), item.deadline, item.max_fires, now, now))
            self._event(c, ident, owner_id, "created", {"predicate": predicate})
        return self.get(owner_id, ident) or {}

    def get(self, owner_id, ident):
        with self.db.transaction() as c:
            row = c.execute("SELECT * FROM meemee_monitors WHERE owner_id=%s AND id=%s", (owner_id, ident)).fetchone()
        if not row:
            return None
        result = dict(row); result["predicate"] = json.loads(result["predicate"]); return result

    def list(self, owner_id, status=None):
        query, values = "SELECT id FROM meemee_monitors WHERE owner_id=%s", [owner_id]
        if status:
            query += " AND status=%s"; values.append(status)
        with self.db.transaction() as c:
            rows = c.execute(query + " ORDER BY created_at DESC,id DESC", values).fetchall()
        return [self.get(owner_id, row["id"]) for row in rows]

    def evaluate(self, owner_id, source_id, event, at=None):
        clock, fired = at or _now().isoformat(), []
        with self.db.transaction() as c:
            rows = c.execute("""SELECT * FROM meemee_monitors WHERE owner_id=%s AND source_id=%s AND status='active'
                                ORDER BY id FOR UPDATE""", (owner_id, source_id)).fetchall()
            for row in rows:
                if row["deadline"] and row["deadline"] <= clock:
                    c.execute("UPDATE meemee_monitors SET status='timed_out',updated_at=%s WHERE id=%s", (clock, row["id"]))
                    self._event(c, row["id"], owner_id, "timed_out", {}); continue
                if self.matches(json.loads(row["predicate"]), event):
                    count = row["fire_count"] + 1
                    status = "completed" if count >= row["max_fires"] else "active"
                    c.execute("UPDATE meemee_monitors SET fire_count=%s,status=%s,updated_at=%s WHERE id=%s", (count, status, clock, row["id"]))
                    self._event(c, row["id"], owner_id, "triggered", event); fired.append(row["id"])
        return fired

    def cancel(self, owner_id, ident):
        with self.db.transaction() as c:
            changed = c.execute("UPDATE meemee_monitors SET status='cancelled',updated_at=%s WHERE owner_id=%s AND id=%s AND status='active'",
                                (_now().isoformat(), owner_id, ident)).rowcount
            if changed:
                self._event(c, ident, owner_id, "cancelled", {})
        return bool(changed)

    def events(self, owner_id, ident):
        with self.db.transaction() as c:
            rows = c.execute("""SELECT sequence,kind,payload,created_at FROM meemee_monitor_events WHERE owner_id=%s AND monitor_id=%s
                                ORDER BY sequence""", (owner_id, ident)).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload"])} for row in rows]

    def delete_owner(self, owner_id: str) -> dict[str, int]:
        if not owner_id:
            raise ValueError("owner is required")
        with self.db.transaction() as c:
            events = c.execute("DELETE FROM meemee_monitor_events WHERE owner_id=%s", (owner_id,)).rowcount
            monitors = c.execute("DELETE FROM meemee_monitors WHERE owner_id=%s", (owner_id,)).rowcount
        return {"monitors": monitors, "monitor_events": events}


class ReflectionSchedule:
    def __init__(self, db: Database):
        self.db = db

    def ping(self) -> bool:
        with self.db.transaction() as c:
            c.execute("SELECT 1 FROM meemee_reflection_runs LIMIT 0")
        return True

    def state(self, owner_id: str) -> dict[str, Any] | None:
        with self.db.transaction() as c:
            row = c.execute("SELECT * FROM meemee_reflection_runs WHERE owner_id=%s", (owner_id,)).fetchone()
        return dict(row) if row else None

    def due(self, watermarks: dict[str, int], interval: timedelta, now: datetime | None = None) -> list[str]:
        now, out = now or _now(), []
        for owner, top in sorted(watermarks.items()):
            st = self.state(owner)
            if st is None:
                out.append(owner); continue
            if top <= st["watermark"]:
                continue
            last = st["last_attempt_at"]
            if last is None or now - datetime.fromisoformat(last) >= interval:
                out.append(owner)
        return out

    def record(self, owner_id: str, status: str, result: dict[str, Any], watermark: int | None, now: datetime | None = None) -> None:
        at = (now or _now()).isoformat()
        with self.db.transaction() as c:
            c.execute("""INSERT INTO meemee_reflection_runs(owner_id,watermark,last_attempt_at,last_success_at,last_status,last_result)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (owner_id) DO UPDATE SET
                watermark=COALESCE(%s, meemee_reflection_runs.watermark), last_attempt_at=EXCLUDED.last_attempt_at,
                last_success_at=COALESCE(EXCLUDED.last_success_at, meemee_reflection_runs.last_success_at),
                last_status=EXCLUDED.last_status, last_result=EXCLUDED.last_result""",
                      (owner_id, watermark or 0, at, at if status == "ok" else None, status,
                       json.dumps(result, sort_keys=True, default=str), watermark))

    def delete_owner(self, owner_id: str) -> int:
        with self.db.transaction() as c:
            return c.execute("DELETE FROM meemee_reflection_runs WHERE owner_id=%s", (owner_id,)).rowcount
