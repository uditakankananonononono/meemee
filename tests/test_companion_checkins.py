from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from meemee.companion.checkins import CheckInScheduler, in_quiet_hours, next_due, slot_name
from meemee.companion.models import CheckInPreferences, QuietHours, UserProfile
from meemee.companion.store import CompanionStore

UTC = timezone.utc


def test_quiet_hours_daytime_window():
    quiet = QuietHours(start="12:00", end="14:00")
    assert in_quiet_hours(datetime(2026, 1, 1, 13, 0, tzinfo=UTC), quiet)
    assert not in_quiet_hours(datetime(2026, 1, 1, 14, 0, tzinfo=UTC), quiet)
    assert not in_quiet_hours(datetime(2026, 1, 1, 9, 0, tzinfo=UTC), quiet)


def test_quiet_hours_overnight_window():
    quiet = QuietHours(start="22:00", end="06:30")
    assert in_quiet_hours(datetime(2026, 1, 1, 23, 15, tzinfo=UTC), quiet)
    assert in_quiet_hours(datetime(2026, 1, 1, 3, 0, tzinfo=UTC), quiet)
    assert not in_quiet_hours(datetime(2026, 1, 1, 12, 0, tzinfo=UTC), quiet)
    assert not in_quiet_hours(datetime(2026, 1, 1, 6, 30, tzinfo=UTC), quiet)


def test_next_due_respects_cadence():
    prefs = CheckInPreferences(enabled=True, cadence_minutes=60)
    after = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    due = next_due(prefs, ZoneInfo("UTC"), after)
    assert due == datetime(2026, 1, 1, 11, 0, tzinfo=UTC)


def test_next_due_skips_quiet_hours_local_time():
    prefs = CheckInPreferences(enabled=True, cadence_minutes=30, quiet_hours=QuietHours(start="22:00", end="06:00"))
    tz = ZoneInfo("Asia/Calcutta")
    after = datetime(2026, 1, 1, 16, 45, tzinfo=UTC)  # 22:15 IST, already quiet
    due = next_due(prefs, tz, after)
    local = due.astimezone(tz)
    assert not in_quiet_hours(local, prefs.quiet_hours)
    assert local.hour == 6 and local.minute == 0 or local.hour == 6


def test_slot_name_is_minute_stable():
    due = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
    assert slot_name(due) == "20260304T0506Z"


def test_scheduler_plans_only_enabled_users(tmp_path: Path):
    store = CompanionStore(tmp_path / "c.db")
    store.upsert_user(UserProfile(user_id="off", display_name="Off"))
    assert CheckInScheduler(store).plan_user("off") is None
    enabled = UserProfile(
        user_id="on", display_name="On",
        checkins=CheckInPreferences(enabled=True, cadence_minutes=30),
    )
    store.upsert_user(enabled)
    after = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    row = CheckInScheduler(store).plan_user("on", after)
    assert row is not None and row["status"] == "queued"
    again = CheckInScheduler(store).plan_user("on", after)
    assert again["id"] == row["id"]
    planned = CheckInScheduler(store).plan_all(after + timedelta(hours=1))
    assert any(item["user_id"] == "on" for item in planned)


def test_scheduler_unknown_user(tmp_path: Path):
    store = CompanionStore(tmp_path / "c.db")
    assert CheckInScheduler(store).plan_user("ghost") is None
