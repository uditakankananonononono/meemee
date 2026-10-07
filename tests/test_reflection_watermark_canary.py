"""A late scheduler completion must never rewind already processed evidence."""
from datetime import datetime, timedelta, timezone

from meemee.reflection_schedule import ReflectionSchedule


def test_late_completion_does_not_regress_watermark(tmp_path):
    early = ReflectionSchedule(tmp_path / 's.db')
    late = ReflectionSchedule(tmp_path / 's.db')
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    late.record('owner', 'ok', {}, 100, now)
    early.record('owner', 'ok', {}, 50, now + timedelta(seconds=1))
    assert early.state('owner')['watermark'] == 100
    assert early.due({'owner': 100}, timedelta(0), now + timedelta(hours=1)) == []
