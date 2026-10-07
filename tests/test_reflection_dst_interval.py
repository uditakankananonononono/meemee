"""Reflection retry intervals are elapsed durations, not repeated wall-clock hours."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from test_monitors_reflection_backends import stores  # noqa: F401


def test_reflection_due_counts_elapsed_fall_back_hour(stores):  # noqa: F811
    _, schedule = stores()
    zone = ZoneInfo('America/New_York')
    first = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
    second = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=1)
    schedule.record('owner', 'failed', {}, None, first)
    assert schedule.due({'owner': 1}, timedelta(hours=1), second) == ['owner']


def test_reflection_due_does_not_count_skipped_spring_hour(stores):  # noqa: F811
    _, schedule = stores()
    zone = ZoneInfo('America/New_York')
    first = datetime(2026, 3, 8, 1, 30, tzinfo=zone)
    second = datetime(2026, 3, 8, 3, 30, tzinfo=zone)
    schedule.record('owner', 'failed', {}, None, first)
    assert schedule.due({'owner': 1}, timedelta(hours=2), second) == []
