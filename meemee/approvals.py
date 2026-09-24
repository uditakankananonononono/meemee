from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


def normalize_expiry(value: str | None) -> str | None:
    """Canonical UTC ISO 8601 form of a grant expiry, shared by the SQLite and PostgreSQL stores.

    Naive timestamps are read as UTC. Anything that is not ISO 8601 raises ValueError, so a typo
    can never become a grant that silently never (or always) expires.
    """
    if value is None:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("expires_at must be an ISO 8601 timestamp") from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def utc_moment(now: datetime | None = None) -> datetime:
    """``now`` (default: the current time) as an aware UTC datetime; naive values are read as UTC."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def constraints_match(constraints: dict | None, arguments: dict | None) -> bool:
    """A grant with no constraints allows any arguments; otherwise every constrained key must match exactly."""
    if constraints is None:
        return True
    supplied = arguments or {}
    return all(key in supplied and supplied[key] == value for key, value in constraints.items())


class ApprovalStore:
    """Persistent, revocable, expiring per-principal grants for exact tool names."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock, self.db:
            self.db.execute("""CREATE TABLE IF NOT EXISTS tool_approvals (
                principal TEXT NOT NULL, tool TEXT NOT NULL, granted_at TEXT NOT NULL,
                expires_at TEXT, revoked_at TEXT, granted_by TEXT NOT NULL,
                argument_constraints TEXT,
                PRIMARY KEY(principal, tool)
            )""")
            columns={row[1] for row in self.db.execute("PRAGMA table_info(tool_approvals)")}
            if "argument_constraints" not in columns:
                self.db.execute("ALTER TABLE tool_approvals ADD COLUMN argument_constraints TEXT")

    def grant(self, principal: str, tool: str, granted_by: str, expires_at: str | None = None, argument_constraints: dict | None = None) -> None:
        if not principal or not tool or not granted_by:
            raise ValueError("principal, tool and granted_by are required")
        expires_at = normalize_expiry(expires_at)
        now = datetime.now(timezone.utc).isoformat()
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO tool_approvals(principal,tool,granted_at,expires_at,revoked_at,granted_by,argument_constraints) VALUES(?,?,?,?,NULL,?,?) ON CONFLICT(principal,tool) DO UPDATE SET granted_at=excluded.granted_at,expires_at=excluded.expires_at,revoked_at=NULL,granted_by=excluded.granted_by,argument_constraints=excluded.argument_constraints",
                (principal, tool, now, expires_at, granted_by, json.dumps(argument_constraints, sort_keys=True) if argument_constraints is not None else None),
            )

    def allows(self, principal: str, tool: str, now: datetime | None = None, arguments: dict | None = None) -> bool:
        current = utc_moment(now).isoformat()
        with self.lock:
            row = self.db.execute(
                "SELECT argument_constraints FROM tool_approvals WHERE principal=? AND tool=? AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>?)",
                (principal, tool, current),
            ).fetchone()
        if row is None: return False
        constraints = json.loads(row["argument_constraints"]) if row["argument_constraints"] else None
        return constraints_match(constraints, arguments)

    def revoke(self, principal: str, tool: str) -> bool:
        with self.lock, self.db:
            changed = self.db.execute(
                "UPDATE tool_approvals SET revoked_at=? WHERE principal=? AND tool=? AND revoked_at IS NULL",
                (datetime.now(timezone.utc).isoformat(), principal, tool),
            ).rowcount
        return bool(changed)

    def active_count(self, principal: str, now: datetime | None = None) -> int:
        current = utc_moment(now).isoformat()
        with self.lock:
            return int(self.db.execute(
                "SELECT count(*) FROM tool_approvals WHERE principal=? AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>?)",
                (principal, current),
            ).fetchone()[0])

    def list(self, principal: str) -> list[dict]:
        with self.lock:
            rows = self.db.execute(
                "SELECT principal,tool,granted_at,expires_at,revoked_at,granted_by,argument_constraints FROM tool_approvals WHERE principal=? ORDER BY tool",
                (principal,),
            ).fetchall()
        return [{**dict(row), "argument_constraints": json.loads(row["argument_constraints"]) if row["argument_constraints"] else None} for row in rows]

    def delete_principal(self, principal: str) -> int:
        """Remove every standing tool grant, active or revoked, held by a principal."""
        with self.lock, self.db:
            return self.db.execute("DELETE FROM tool_approvals WHERE principal=?", (principal,)).rowcount

