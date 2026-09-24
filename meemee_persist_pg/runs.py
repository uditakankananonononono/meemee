"""PostgreSQL completed-run history: the same contract as ``meemee.runs.RunStore``.

With per-host runs.sqlite3 a run finished on one host was invisible (404, missing from lists) on
every other host. Here all hosts write and read one table; list order and cursors match SQLite:
newest first by (created_at, run_id), cursors encode that pair.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from psycopg.types.json import Jsonb

from meemee.cursors import decode_cursor, encode_cursor
from meemee.types import RunReport

from ._db import Database


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _moment(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class RunStore:
    """Principal-owned completed run reports for durable account history (PostgreSQL)."""

    def __init__(self, db: Database):
        self.db = db

    def add(self, principal: str, report: RunReport) -> None:
        with self.db.transaction() as c:
            c.execute(
                """INSERT INTO meemee_runs(run_id, principal, goal, final, steps_used, tool_results, created_at, approvals_required)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (report.run_id, principal, report.goal, report.final, report.steps_used,
                 Jsonb(json.loads(json.dumps(report.tool_results))), datetime.now(timezone.utc),
                 Jsonb([item.model_dump() for item in report.approvals_required])),
            )

    @staticmethod
    def _row(row: dict[str, Any]) -> dict:
        result = dict(row)
        result["created_at"] = _iso(result["created_at"])
        result["approvals_required"] = result.get("approvals_required") or []
        result["blocked"] = bool(result["approvals_required"])
        return result

    def get(self, principal: str, run_id: str) -> dict | None:
        with self.db.transaction() as c:
            row = c.execute("SELECT * FROM meemee_runs WHERE principal=%s AND run_id=%s", (principal, run_id)).fetchone()
        return self._row(row) if row else None

    def list(self, principal: str, before: str | None = None, limit: int = 100,
             cursor: str | None = None) -> tuple[list[dict], str | None]:
        query = "SELECT * FROM meemee_runs WHERE principal=%s"
        params: list = [principal]
        if before is not None:
            query += " AND created_at < %s"; params.append(_moment(before))
        if cursor is not None:
            cursor_time, cursor_id = decode_cursor(cursor)
            moment = _moment(cursor_time)
            query += " AND (created_at < %s OR (created_at = %s AND run_id < %s))"
            params.extend((moment, moment, cursor_id))
        page_size = min(max(limit, 1), 500)
        query += " ORDER BY created_at DESC, run_id DESC LIMIT %s"; params.append(page_size + 1)
        with self.db.transaction() as c:
            rows = c.execute(query, tuple(params)).fetchall()
        items = [self._row(row) for row in rows[:page_size]]
        next_cursor = encode_cursor(items[-1]["created_at"], items[-1]["run_id"]) if len(rows) > page_size else None
        return items, next_cursor

    def run_ids(self, principal: str) -> list[str]:
        with self.db.transaction() as c:
            return [r["run_id"] for r in c.execute("SELECT run_id FROM meemee_runs WHERE principal=%s", (principal,)).fetchall()]

    def delete_principal(self, principal: str) -> int:
        if not principal:
            raise ValueError("principal is required")
        with self.db.transaction() as c:
            return c.execute("DELETE FROM meemee_runs WHERE principal=%s", (principal,)).rowcount

    def ping(self) -> bool:
        with self.db.transaction() as c:
            c.execute("SELECT 1 FROM meemee_runs LIMIT 0")
        return True
