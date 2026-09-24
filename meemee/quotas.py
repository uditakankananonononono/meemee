from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


class QuotaExceeded(ValueError):
    pass


class QuotaStore:
    """Atomic per-principal daily quotas shared by all supported-host processes."""

    def __init__(self, path: Path, default_daily_jobs: int = 100):
        if default_daily_jobs < 1:
            raise ValueError("default quota must be positive")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.default = default_daily_jobs
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS quota_limits (
                principal TEXT PRIMARY KEY, daily_jobs INTEGER NOT NULL CHECK(daily_jobs>0)
            );
            CREATE TABLE IF NOT EXISTS quota_usage (
                principal TEXT NOT NULL, day TEXT NOT NULL, jobs INTEGER NOT NULL,
                PRIMARY KEY(principal, day)
            );
        """)

    def limit(self, principal: str) -> int:
        row = self.db.execute("SELECT daily_jobs FROM quota_limits WHERE principal=?", (principal,)).fetchone()
        return int(row[0]) if row else self.default

    def set_limit(self, principal: str, daily_jobs: int) -> None:
        if daily_jobs < 1:
            raise ValueError("daily quota must be positive")
        with self.lock, self.db:
            self.db.execute("INSERT INTO quota_limits VALUES(?,?) ON CONFLICT(principal) DO UPDATE SET daily_jobs=excluded.daily_jobs", (principal, daily_jobs))

    def consume_job(self, principal: str, now: datetime | None = None) -> dict[str, int | str]:
        day = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()
        maximum = self.limit(principal)
        with self.lock, self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("INSERT INTO quota_usage VALUES(?,?,1) ON CONFLICT(principal,day) DO UPDATE SET jobs=jobs+1", (principal, day))
            used = int(self.db.execute("SELECT jobs FROM quota_usage WHERE principal=? AND day=?", (principal, day)).fetchone()[0])
            if used > maximum:
                self.db.execute("ROLLBACK")
                raise QuotaExceeded(f"daily job quota exceeded ({maximum})")
            self.db.execute("COMMIT")
        return {"day": day, "used": used, "limit": maximum, "remaining": maximum-used}

    def status(self, principal: str, now: datetime | None = None) -> dict[str, int | str]:
        day = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()
        maximum = self.limit(principal)
        row = self.db.execute("SELECT jobs FROM quota_usage WHERE principal=? AND day=?", (principal, day)).fetchone()
        used = int(row[0]) if row else 0
        return {"day": day, "used": used, "limit": maximum, "remaining": max(maximum-used, 0)}

    def delete_principal(self, principal: str) -> dict[str, int]:
        with self.lock, self.db:
            usage = self.db.execute("DELETE FROM quota_usage WHERE principal=?", (principal,)).rowcount
            limits = self.db.execute("DELETE FROM quota_limits WHERE principal=?", (principal,)).rowcount
        return {"quota_usage": usage, "quota_limits": limits}

