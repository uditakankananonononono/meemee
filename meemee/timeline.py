from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class TimelineEvent:
    id: int
    owner_id: str
    source_id: str
    external_id: str
    kind: str
    title: str
    content: str
    occurred_at: str
    provenance: dict[str, Any]
    metadata: dict[str, Any]
    ingested_at: str


class Timeline:
    """Read-only, owner-scoped chronological view over ContextStore records."""

    def __init__(self, context_store: Any):
        self.store = context_store

    @staticmethod
    def _event(row: sqlite3.Row) -> TimelineEvent:
        return TimelineEvent(
            id=int(row["id"]),
            owner_id=row["owner_id"],
            source_id=row["source_id"],
            external_id=row["external_id"],
            kind=row["kind"],
            title=row["title"],
            content=row["content"],
            occurred_at=row["occurred_at"],
            provenance=json.loads(row["provenance"]),
            metadata=json.loads(row["metadata"]),
            ingested_at=row["ingested_at"],
        )

    def range(
        self,
        owner_id: str,
        *,
        start: str | None = None,
        end: str | None = None,
        sources: Iterable[str] | None = None,
        kinds: Iterable[str] | None = None,
        limit: int = 100,
        before: tuple[str, int] | None = None,
    ) -> list[TimelineEvent]:
        if not owner_id:
            raise ValueError("owner_id is required")
        clauses = ["owner_id=?"]
        args: list[Any] = [owner_id]
        if start is not None:
            clauses.append("occurred_at>=?")
            args.append(start)
        if end is not None:
            clauses.append("occurred_at<?")
            args.append(end)
        if before is not None:
            clauses.append("(occurred_at<? OR (occurred_at=? AND id<?))")
            args.extend((before[0], before[0], before[1]))
        for column, values in (("source_id", sources), ("kind", kinds)):
            selected = sorted(set(values or []))
            if selected:
                clauses.append(f"{column} IN ({','.join('?' for _ in selected)})")
                args.extend(selected)
        args.append(max(1, min(int(limit), 500)))
        sql = f"SELECT * FROM context_records WHERE {' AND '.join(clauses)} ORDER BY occurred_at DESC,id DESC LIMIT ?"
        with self.store.lock:
            rows = self.store.db.execute(sql, args).fetchall()
        return [self._event(row) for row in rows]

    def around(
        self, owner_id: str, occurred_at: str, *, seconds: int = 300, limit: int = 100
    ) -> list[TimelineEvent]:
        if seconds < 0:
            raise ValueError("seconds must be non-negative")
        anchor = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        from datetime import timedelta

        start = (anchor - timedelta(seconds=seconds)).isoformat()
        end = (anchor + timedelta(seconds=seconds, microseconds=1)).isoformat()
        return self.range(owner_id, start=start, end=end, limit=limit)

    def versions(self, owner_id: str, source_id: str, external_id: str) -> list[TimelineEvent]:
        with self.store.lock:
            rows = self.store.db.execute(
                "SELECT * FROM context_records WHERE owner_id=? AND source_id=? AND external_id=? ORDER BY ingested_at,id",
                (owner_id, source_id, external_id),
            ).fetchall()
        return [self._event(row) for row in rows]

    def source_summary(self, owner_id: str) -> list[dict[str, Any]]:
        with self.store.lock:
            rows = self.store.db.execute(
                """SELECT source_id,count(*) event_count,min(occurred_at) first_event_at,
                max(occurred_at) last_event_at,max(ingested_at) last_ingested_at
                FROM context_records WHERE owner_id=? GROUP BY source_id ORDER BY source_id""",
                (owner_id,),
            ).fetchall()
        return [dict(row) for row in rows]
