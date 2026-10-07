"""Late source checks must not rewind freshness or hide newer failures."""
from datetime import datetime, timezone

from meemee.source_health import SourceHealthStore


def test_late_success_does_not_rewind_freshness_or_cursor(tmp_path):
    store = SourceHealthStore(tmp_path / 'h.db')
    store.record('u', 'mail', ok=True, cursor='new', checked_at='2026-10-07T10:00:00+00:00')
    store.record('u', 'mail', ok=True, cursor='old', checked_at='2026-10-07T09:00:00+00:00')
    row = store.status('u', 'mail', now=datetime(2026, 10, 7, 10, 5, tzinfo=timezone.utc))
    assert row['status'] == 'healthy'
    assert row['last_cursor'] == 'new'
    assert row['total_successes'] == 2


def test_late_success_does_not_clear_newer_failure_streak(tmp_path):
    store = SourceHealthStore(tmp_path / 'h.db')
    for minute in [1, 2, 3]:
        store.record('u', 'mail', ok=False, error='timeout', checked_at=f'2026-10-07T10:0{minute}:00+00:00')
    store.record('u', 'mail', ok=True, checked_at='2026-10-07T10:00:00+00:00')
    row = store.status('u', 'mail', now=datetime(2026, 10, 7, 10, 5, tzinfo=timezone.utc))
    assert row['status'] == 'down'
    assert row['consecutive_failures'] == 3
    assert row['last_error'] == 'timeout'
    assert row['last_attempt_at'] == '2026-10-07T10:03:00+00:00'
