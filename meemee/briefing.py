from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass(frozen=True)
class BriefingSection:
    key: str
    title: str
    items: list[dict[str, Any]]


@dataclass(frozen=True)
class DailyBriefing:
    owner_id: str
    generated_at: str
    period_start: str
    period_end: str
    sections: list[BriefingSection]
    source_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["item_count"] = sum(len(section.items) for section in self.sections)
        return result


class BriefingBuilder:
    """Deterministic daily briefing assembled from existing read interfaces."""

    def __init__(self, timeline: Any, personal_model: Any | None = None):
        self.timeline = timeline
        self.personal_model = personal_model

    @staticmethod
    def _event_item(event: Any) -> dict[str, Any]:
        raw = asdict(event) if hasattr(event, "__dataclass_fields__") else dict(event)
        return {
            key: raw[key]
            for key in (
                "id",
                "source_id",
                "external_id",
                "kind",
                "title",
                "content",
                "occurred_at",
                "provenance",
            )
            if key in raw
        }

    def build(
        self,
        owner_id: str,
        *,
        at: datetime | None = None,
        lookback_hours: int = 24,
        max_events: int = 50,
        priority_kinds: Iterable[str] = ("goal", "project", "constraint"),
    ) -> DailyBriefing:
        if not owner_id:
            raise ValueError("owner_id is required")
        if lookback_hours <= 0 or max_events <= 0:
            raise ValueError("lookback_hours and max_events must be positive")
        clock = at or datetime.now(timezone.utc)
        if clock.tzinfo is None:
            clock = clock.replace(tzinfo=timezone.utc)
        start = clock - timedelta(hours=lookback_hours)
        events = self.timeline.range(
            owner_id, start=start.isoformat(), end=clock.isoformat(), limit=max_events
        )
        event_items = [self._event_item(event) for event in events]
        sections: list[BriefingSection] = []
        if event_items:
            sections.append(BriefingSection("timeline", "Since your last briefing", event_items))
        if self.personal_model is not None:
            wanted = set(priority_kinds)
            priorities = []
            for item in self.personal_model.list(owner_id):
                if item.get("kind") in wanted:
                    priorities.append(
                        {
                            key: item.get(key)
                            for key in ("id", "kind", "title", "value", "confidence", "evidence")
                        }
                    )
            priorities.sort(
                key=lambda item: (-float(item.get("confidence") or 0), item.get("title") or "")
            )
            if priorities:
                sections.append(BriefingSection("priorities", "Active priorities", priorities))
        sources = sorted({item["source_id"] for item in event_items})
        return DailyBriefing(
            owner_id, clock.isoformat(), start.isoformat(), clock.isoformat(), sections, sources
        )
