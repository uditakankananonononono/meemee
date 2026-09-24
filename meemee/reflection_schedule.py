"""Scheduled personal-model reflection on the ``reflection`` model route.

Each pass reflects only owners whose context ledger has records newer than their last
successful run and whose interval has elapsed. Failures are recorded and retried on the
next due pass; they never advance the watermark.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .context import ContextStore
from .model_profiles import last_model_trace
from .personal_model import PersonalModelStore
from .reflection import PersonalModelReflector, ReflectionModel

log = logging.getLogger("meemee.reflection")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ReflectionSchedule:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock, self.db:
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA busy_timeout=5000;
                CREATE TABLE IF NOT EXISTS reflection_runs(
                  owner_id TEXT PRIMARY KEY,
                  watermark INTEGER NOT NULL DEFAULT 0,
                  last_attempt_at TEXT,
                  last_success_at TEXT,
                  last_status TEXT,
                  last_result TEXT
                );
            """)

    def state(self, owner_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM reflection_runs WHERE owner_id=?", (owner_id,)).fetchone()
        return dict(row) if row else None

    def due(self, watermarks: dict[str, int], interval: timedelta, now: datetime | None = None) -> list[str]:
        now = now or _now()
        out = []
        for owner, top in sorted(watermarks.items()):
            st = self.state(owner)
            if st is None:
                out.append(owner)
                continue
            if top <= st["watermark"]:
                continue
            last = st["last_attempt_at"]
            if last is None or now - datetime.fromisoformat(last) >= interval:
                out.append(owner)
        return out

    def record(self, owner_id: str, status: str, result: dict[str, Any], watermark: int | None, now: datetime | None = None) -> None:
        at = (now or _now()).isoformat()
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO reflection_runs(owner_id,watermark,last_attempt_at,last_success_at,last_status,last_result)"
                " VALUES(?,?,?,?,?,?) ON CONFLICT(owner_id) DO UPDATE SET"
                " watermark=COALESCE(?, watermark), last_attempt_at=excluded.last_attempt_at,"
                " last_success_at=COALESCE(excluded.last_success_at, last_success_at),"
                " last_status=excluded.last_status, last_result=excluded.last_result",
                (owner_id, watermark or 0, at, at if status == "ok" else None, status,
                 json.dumps(result, sort_keys=True, default=str), watermark),
            )

    def delete_owner(self, owner_id: str) -> int:
        with self.lock, self.db:
            return self.db.execute("DELETE FROM reflection_runs WHERE owner_id=?", (owner_id,)).rowcount


async def reflect_due_once(
    context: ContextStore,
    personal: PersonalModelStore,
    model: ReflectionModel,
    schedule: ReflectionSchedule,
    interval: timedelta,
    audit: Any | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    watermarks = context.owner_watermarks()
    reflector = PersonalModelReflector(context, personal, model)
    summaries = []
    for owner in schedule.due(watermarks, interval, now):
        try:
            result = await reflector.reflect(owner)
        except Exception as exc:  # noqa: BLE001 - one owner's failure must not stop the pass
            info = {"error": f"{type(exc).__name__}: {str(exc)[:300]}", "model_trace": last_model_trace(model)}
            schedule.record(owner, "failed", info, None, now)
            log.warning("scheduled reflection failed for %s: %s", owner, info["error"])
            summary = {"owner_id": owner, "status": "failed", **info}
        else:
            info = {"considered": result["considered"], "accepted": result["accepted"],
                    "rejected": result["rejected"], "model_trace": last_model_trace(model)}
            schedule.record(owner, "ok", info, watermarks[owner], now)
            summary = {"owner_id": owner, "status": "ok", **info}
        if audit is not None:
            audit.append("reflection-scheduler", "personal_model.reflect.scheduled", owner, summary["status"],
                         {k: v for k, v in summary.items() if k != "owner_id"})
        summaries.append(summary)
    return summaries


async def reflection_forever(settings: Any | None = None) -> None:
    from .config import Settings
    from .model_profiles import build_role_model
    from .persistence import persistence_from_settings

    settings = settings or Settings()
    if settings.reflection_interval_minutes <= 0:
        raise ValueError("scheduled reflection is disabled (MEEMEE_REFLECTION_INTERVAL_MINUTES <= 0)")
    persistence = persistence_from_settings(settings)
    context = persistence.context  # PostgreSQL mode: shared with every API host
    personal = persistence.personal_model
    schedule = ReflectionSchedule(settings.data_dir / "reflection-schedule.sqlite3")
    audit = persistence.audit  # PostgreSQL mode: the shared global chain
    model = build_role_model(settings, "reflection")
    interval = timedelta(minutes=settings.reflection_interval_minutes)
    try:
        while True:
            summaries = await reflect_due_once(context, personal, model, schedule, interval, audit)
            if summaries:
                log.info("scheduled reflection pass", extra={"owners": len(summaries)})
            await asyncio.sleep(settings.reflection_poll_seconds)
    finally:
        await model.aclose()
        persistence.close()
