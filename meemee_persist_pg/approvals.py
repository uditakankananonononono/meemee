"""PostgreSQL tool-approval grants: the same contract as ``meemee.approvals.ApprovalStore``.

In PostgreSQL mode the API and every worker read and write grants here, so a grant made through
``PUT /v1/approvals/{principal}`` on one host is enforced by a worker on another host at its next
tool call. The SQLite store keeps grants in ``approvals.sqlite3`` on local disk and is only correct
when the API and workers share one data directory.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from psycopg.types.json import Jsonb

from meemee.approvals import constraints_match, normalize_expiry, utc_moment

from ._db import Database

_ACTIVE = "revoked_at IS NULL AND (expires_at IS NULL OR expires_at > %s)"


def _iso(value: datetime | None) -> str | None:
    # UTC regardless of the server/session TimeZone, like the SQLite store.
    return value.astimezone(timezone.utc).isoformat() if value is not None else None


class ApprovalStore:
    """Persistent, revocable, expiring per-principal grants for exact tool names (PostgreSQL)."""

    def __init__(self, db: Database):
        self.db = db

    def grant(self, principal: str, tool: str, granted_by: str, expires_at: str | None = None,
              argument_constraints: dict | None = None) -> None:
        if not principal or not tool or not granted_by:
            raise ValueError("principal, tool and granted_by are required")
        if argument_constraints is not None and not isinstance(argument_constraints, dict):
            raise ValueError("argument_constraints must be an object")
        expiry = normalize_expiry(expires_at)
        with self.db.transaction() as c:
            c.execute(
                """INSERT INTO meemee_tool_approvals(principal,tool,granted_at,expires_at,revoked_at,granted_by,argument_constraints)
                   VALUES(%s,%s,%s,%s,NULL,%s,%s)
                   ON CONFLICT(principal,tool) DO UPDATE SET granted_at=excluded.granted_at, expires_at=excluded.expires_at,
                     revoked_at=NULL, granted_by=excluded.granted_by, argument_constraints=excluded.argument_constraints""",
                (principal, tool, utc_moment(), expiry, granted_by,
                 Jsonb(argument_constraints) if argument_constraints is not None else None),
            )

    def allows(self, principal: str, tool: str, now: datetime | None = None, arguments: dict | None = None) -> bool:
        with self.db.transaction() as c:
            row = c.execute(
                f"SELECT argument_constraints FROM meemee_tool_approvals WHERE principal=%s AND tool=%s AND {_ACTIVE}",
                (principal, tool, utc_moment(now)),
            ).fetchone()
        if row is None:
            return False
        return constraints_match(row["argument_constraints"], arguments)

    def revoke(self, principal: str, tool: str) -> bool:
        with self.db.transaction() as c:
            changed = c.execute(
                "UPDATE meemee_tool_approvals SET revoked_at=%s WHERE principal=%s AND tool=%s AND revoked_at IS NULL",
                (utc_moment(), principal, tool),
            ).rowcount
        return bool(changed)

    def active_count(self, principal: str, now: datetime | None = None) -> int:
        with self.db.transaction() as c:
            row = c.execute(
                f"SELECT count(*) AS n FROM meemee_tool_approvals WHERE principal=%s AND {_ACTIVE}",
                (principal, utc_moment(now)),
            ).fetchone()
        return int(row["n"])

    def list(self, principal: str) -> list[dict[str, Any]]:
        with self.db.transaction() as c:
            rows = c.execute(
                """SELECT principal,tool,granted_at,expires_at,revoked_at,granted_by,argument_constraints
                   FROM meemee_tool_approvals WHERE principal=%s ORDER BY tool""",
                (principal,),
            ).fetchall()
        # Same wire shape as the SQLite store: ISO 8601 strings and a decoded constraints object.
        return [{**row, "granted_at": _iso(row["granted_at"]), "expires_at": _iso(row["expires_at"]),
                 "revoked_at": _iso(row["revoked_at"])} for row in rows]

    def delete_principal(self, principal: str) -> int:
        """Remove every standing tool grant, active or revoked, held by a principal."""
        with self.db.transaction() as c:
            return int(c.execute("DELETE FROM meemee_tool_approvals WHERE principal=%s", (principal,)).rowcount)
