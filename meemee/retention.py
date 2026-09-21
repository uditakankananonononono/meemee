from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass(frozen=True)
class RetentionReport:
    job_events: int = 0
    terminal_jobs: int = 0
    memories: int = 0
    audit_entries: int = 0
    idempotency: int = 0
    rate_limits: int = 0


class RetentionManager:
    """Applies explicit data-retention windows in bounded transactions."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    @staticmethod
    def delete(path: Path, statement: str, parameters: tuple) -> int:
        if not path.exists():
            return 0
        db = sqlite3.connect(path, timeout=5)
        try:
            with db:
                return db.execute(statement, parameters).rowcount
        finally:
            db.close()

    def run(self, now: datetime | None = None, jobs_days: int = 30, memory_days: int = 90, audit_days: int = 365) -> RetentionReport:
        if min(jobs_days, memory_days, audit_days) < 1:
            raise ValueError("retention windows must be at least one day")
        now = now or datetime.now(timezone.utc)
        jobs_cutoff = (now - timedelta(days=jobs_days)).isoformat()
        memory_cutoff = (now - timedelta(days=memory_days)).isoformat()
        _audit_cutoff = (now - timedelta(days=audit_days)).isoformat()
        job_events = self.delete(
            self.data_dir / "jobs.sqlite3",
            "DELETE FROM job_events WHERE job_id IN (SELECT id FROM jobs WHERE status IN ('done','failed','cancelled') AND updated_at<?)",
            (jobs_cutoff,),
        )
        terminal_jobs = self.delete(
            self.data_dir / "jobs.sqlite3",
            "DELETE FROM jobs WHERE status IN ('done','failed','cancelled') AND updated_at<?",
            (jobs_cutoff,),
        )
        memories = self.delete(self.data_dir / "meemee.sqlite3", "DELETE FROM memories WHERE created_at<?", (memory_cutoff,))
        # Tamper-evident audit chains are intentionally retained; pruning needs signed anchors.
        audit_entries = 0
        idempotency = self.delete(self.data_dir / "idempotency.sqlite3", "DELETE FROM idempotency WHERE expires_at<=?", (now.isoformat(),))
        rate_limits = self.delete(self.data_dir / "rate-limits.sqlite3", "DELETE FROM rate_limits WHERE window_start<?", (int(now.timestamp()) - 86400,))
        return RetentionReport(job_events, terminal_jobs, memories, audit_entries, idempotency, rate_limits)
