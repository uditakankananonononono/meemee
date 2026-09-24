"""Product-wide account deletion.

Deleting an account used to disable the identity and wipe companion content only; jobs,
run reports, agent memory, personal model, connected context, monitors, webhooks, quotas,
plans, idempotency records and standing tool approvals stayed behind. ``AccountPurger``
removes all of them for one principal.

The stores live in separate SQLite files (or PostgreSQL for jobs/memory), so one global
transaction is impossible. Instead every step is idempotent and recorded in a durable
ledger: a deletion is opened before the first step, each finished step is written with
its counts, and ``resume_incomplete`` re-runs unfinished deletions after a crash. The
tamper-evident audit log is deliberately retained; it records actions, not content.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FORMAT = "meemee.account-deletion.v1"


@dataclass
class PurgeTargets:
    jobs: Any
    runs: Any
    memory: Any
    idempotency: Any = None
    quotas: Any = None
    entitlements: Any = None
    approvals: Any = None
    monitors: Any = None
    personal_model: Any = None
    context: Any = None
    webhooks: Any = None
    companion: Any = None
    browser_sessions: Any = None
    browser_notices: Any = None
    reflection_schedule: Any = None


class DeletionLedger:
    """Durable record of account deletions and their completed steps."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA busy_timeout=5000;
            CREATE TABLE IF NOT EXISTS account_deletions(
              id TEXT PRIMARY KEY, principal TEXT NOT NULL, requested_by TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('in_progress','completed')),
              started_at TEXT NOT NULL, completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS account_deletions_principal ON account_deletions(principal,status);
            CREATE TABLE IF NOT EXISTS account_deletion_steps(
              deletion_id TEXT NOT NULL, step TEXT NOT NULL, counts TEXT NOT NULL, finished_at TEXT NOT NULL,
              PRIMARY KEY(deletion_id, step)
            );
        """)

    def open(self, principal: str, requested_by: str) -> str:
        with self.lock:
            row = self.db.execute(
                "SELECT id FROM account_deletions WHERE principal=? AND status='in_progress'", (principal,)
            ).fetchone()
            if row:
                return row["id"]
            ident = uuid.uuid4().hex
            self.db.execute(
                "INSERT INTO account_deletions VALUES(?,?,?,'in_progress',?,NULL)",
                (ident, principal, requested_by, _now()),
            )
            return ident

    def done_steps(self, deletion_id: str) -> dict[str, dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT step,counts FROM account_deletion_steps WHERE deletion_id=?", (deletion_id,)
            ).fetchall()
        return {row["step"]: json.loads(row["counts"]) for row in rows}

    def record_step(self, deletion_id: str, step: str, counts: dict) -> None:
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO account_deletion_steps VALUES(?,?,?,?)",
                (deletion_id, step, json.dumps(counts, sort_keys=True), _now()),
            )

    def complete(self, deletion_id: str) -> None:
        with self.lock:
            self.db.execute(
                "UPDATE account_deletions SET status='completed', completed_at=? WHERE id=?", (_now(), deletion_id)
            )

    def get(self, deletion_id: str) -> dict | None:
        with self.lock:
            row = self.db.execute("SELECT * FROM account_deletions WHERE id=?", (deletion_id,)).fetchone()
        if not row:
            return None
        return {**dict(row), "steps": self.done_steps(deletion_id)}

    def incomplete(self) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM account_deletions WHERE status='in_progress' ORDER BY started_at"
            ).fetchall()
        return [dict(row) for row in rows]

    def deleted_since(self, principal: str, since: str) -> bool:
        """True when a deletion for the principal was opened at or after ``since`` (ISO UTC)."""
        with self.lock:
            return self.db.execute(
                "SELECT 1 FROM account_deletions WHERE principal=? AND started_at>=? LIMIT 1", (principal, since)
            ).fetchone() is not None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_counts(value: Any, key: str) -> dict[str, int]:
    if isinstance(value, dict):
        return {k: int(v) for k, v in value.items()}
    return {key: int(value)}


class AccountPurger:
    """Delete everything Meemee stores for one principal, resumably."""

    def __init__(self, targets: PurgeTargets, ledger: DeletionLedger):
        self.targets = targets
        self.ledger = ledger

    def _steps(self, principal: str) -> list[tuple[str, Callable[[], dict[str, int]]]]:
        t = self.targets
        steps: list[tuple[str, Callable[[], dict[str, int]]]] = []

        def memory_step() -> dict[str, int]:
            run_ids = list(t.runs.run_ids(principal)) + list(t.jobs.run_ids_for_principal(principal))
            return {"memories": int(t.memory.delete_runs(run_ids)), "runs_scanned": len(set(run_ids))}

        # memory must run before jobs/runs: it reads their run IDs.
        steps.append(("memory", memory_step))
        steps.append(("jobs", lambda: _as_counts(t.jobs.purge_principal(principal), "jobs_deleted")))
        steps.append(("runs", lambda: _as_counts(t.runs.delete_principal(principal), "runs")))
        optional = [
            ("idempotency", t.idempotency, "delete_principal", "idempotency_records"),
            ("quotas", t.quotas, "delete_principal", "quota_rows"),
            ("entitlements", t.entitlements, "delete_principal", "plans"),
            ("approvals", t.approvals, "delete_principal", "tool_approvals"),
            ("monitors", t.monitors, "delete_owner", "monitors"),
            ("personal_model", t.personal_model, "purge_owner", "personal_items"),
            ("context", t.context, "purge_owner", "context_records"),
            ("webhooks", t.webhooks, "delete_principal", "webhook_subscriptions"),
            ("companion", t.companion, "delete_user_data", "companion_records"),
            ("browser_sessions", t.browser_sessions, "delete_owner", "browser_sessions"),
            ("browser_notices", t.browser_notices, "delete_user", "browser_notices"),
            ("reflection_schedule", t.reflection_schedule, "delete_owner", "reflection_runs"),
        ]
        for name, store, method, key in optional:
            if store is not None:
                fn = getattr(store, method)
                steps.append((name, lambda fn=fn, key=key: _as_counts(fn(principal), key)))
        return steps

    def purge(self, principal: str, requested_by: str) -> dict:
        if not principal:
            raise ValueError("principal is required")
        deletion_id = self.ledger.open(principal, requested_by)
        return self._run(deletion_id, principal)

    def _run(self, deletion_id: str, principal: str) -> dict:
        done = self.ledger.done_steps(deletion_id)
        for name, step in self._steps(principal):
            if name in done:
                continue
            counts = step()
            self.ledger.record_step(deletion_id, name, counts)
        self.ledger.complete(deletion_id)
        record = self.ledger.get(deletion_id) or {}
        totals: dict[str, int] = {}
        for counts in record.get("steps", {}).values():
            for key, value in counts.items():
                if key != "runs_scanned":
                    totals[key] = totals.get(key, 0) + int(value)
        return {"format": FORMAT, "deletion_id": deletion_id, "principal": principal,
                "status": record.get("status"), "steps": record.get("steps", {}), "deleted": totals}

    def resume_incomplete(self) -> list[dict]:
        return [self._run(row["id"], row["principal"]) for row in self.ledger.incomplete()]

    def discard_late_run(self, principal: str, report: Any, started_at: str) -> bool:
        """Drop a synchronous run whose owner deleted their account while it was running."""
        if not self.ledger.deleted_since(principal, started_at):
            return False
        self.targets.memory.delete_runs([report.run_id])
        return True


def build_account_purger(settings: Any, persistence: Any) -> AccountPurger:
    """Wire every principal-owned store from settings (used by the CLI and workers)."""
    from .approvals import ApprovalStore
    from .browser_notices import TakeoverNoticeQueue
    from .browser_sessions import BrowserSessionStore
    from .companion.store import CompanionStore
    from .context import ContextStore
    from .entitlements import EntitlementStore
    from .idempotency import IdempotencyStore
    from .monitors import MonitorStore
    from .personal_model import PersonalModelStore
    from .quotas import QuotaStore
    from .reflection_schedule import ReflectionSchedule
    from .runs import RunStore
    from .webhooks import WebhookStore

    root = settings.data_dir
    targets = PurgeTargets(
        jobs=persistence.jobs, runs=RunStore(root / "runs.sqlite3"), memory=persistence.memory,
        idempotency=IdempotencyStore(root / "idempotency.sqlite3"),
        quotas=QuotaStore(root / "quotas.sqlite3", settings.default_daily_jobs),
        entitlements=EntitlementStore(root / "entitlements.sqlite3", settings.default_plan),
        approvals=ApprovalStore(root / "approvals.sqlite3"), monitors=MonitorStore(root / "monitors.sqlite3"),
        personal_model=PersonalModelStore(root / "personal-model.sqlite3"), context=ContextStore(root / "context.sqlite3"),
        webhooks=WebhookStore(root / "webhooks.sqlite3", settings.webhook_max_payload_bytes, settings.vault_key),
        companion=CompanionStore(root / "companion.sqlite3"),
        browser_sessions=BrowserSessionStore(root / "browser-sessions.sqlite3"),
        browser_notices=TakeoverNoticeQueue(root / "browser-notices.sqlite3"),
        reflection_schedule=ReflectionSchedule(root / "reflection-schedule.sqlite3"),
    )
    return AccountPurger(targets, DeletionLedger(root / "account-deletions.sqlite3"))
