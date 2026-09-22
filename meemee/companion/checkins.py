from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Any

from .models import CheckInPreferences, QuietHours
from .store import CompanionStore


def _hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def in_quiet_hours(moment: datetime, quiet: QuietHours) -> bool:
    """True when the local wall-clock time falls inside the quiet window."""
    local = moment.timetz().replace(tzinfo=None)
    start, end = _hhmm(quiet.start), _hhmm(quiet.end)
    if start == end:
        return True
    if start < end:
        return start <= local < end
    return local >= start or local < end


def next_due(prefs: CheckInPreferences, tz, after: datetime) -> datetime:
    """Next check-in instant strictly after `after`, honoring quiet hours."""
    candidate = after.astimezone(tz) + timedelta(minutes=prefs.cadence_minutes)
    if prefs.quiet_hours is None:
        return candidate.astimezone(timezone.utc)
    guard = 0
    while in_quiet_hours(candidate, prefs.quiet_hours):
        candidate += timedelta(minutes=15)
        guard += 1
        if guard > 4 * 24 * 14:
            raise ValueError("quiet hours leave no deliverable slot in two weeks")
    return candidate.astimezone(timezone.utc)


def slot_name(due: datetime) -> str:
    return due.astimezone(timezone.utc).strftime("%Y%m%dT%H%MZ")


class CheckInScheduler:
    """Plans durable, idempotent check-in slots from per-user preferences."""

    def __init__(self, store: CompanionStore):
        self.store = store

    def plan_user(self, user_id: str, after: datetime | None = None) -> dict[str, Any] | None:
        profile = self.store.profile(user_id)
        if profile is None or not profile.checkins.enabled:
            return None
        moment = after or datetime.now(timezone.utc)
        due = next_due(profile.checkins, profile.tz(), moment)
        row, _created = self.store.schedule_checkin(
            user_id, due, slot_name(due), profile.checkins.channel, profile.checkins.address
        )
        return row

    def plan_all(self, after: datetime | None = None) -> list[dict[str, Any]]:
        planned = []
        for record in self.store.list_users(limit=500):
            row = self.plan_user(record["user_id"], after)
            if row is not None:
                planned.append(row)
        return planned
