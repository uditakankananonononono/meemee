"""Pre-normalization rows with offsets must compare by time, not ISO text."""
from datetime import datetime, timezone

from meemee.source_health import SourceHealthStore


def test_legacy_positive_offset_does_not_block_newer_utc_check(tmp_path):
    store = SourceHealthStore(tmp_path / 'h.db')
    store.record('u', 'mail', ok=True, cursor='old', checked_at='2026-10-07T09:00:00+00:00')
    # Simulate an existing row written before UTC normalization was introduced.
    with store.db:
        store.db.execute("UPDATE source_health SET last_attempt_at='2026-10-07T14:30:00+05:30',last_success_at='2026-10-07T14:30:00+05:30'")
        store.db.execute("UPDATE source_health_checks SET checked_at='2026-10-07T14:30:00+05:30'")
    store.record('u', 'mail', ok=True, cursor='new', checked_at='2026-10-07T10:00:00+00:00')
    row = store.status('u', 'mail', now=datetime(2026, 10, 7, 10, 5, tzinfo=timezone.utc))
    assert row['status'] == 'healthy'
    assert row['last_cursor'] == 'new'


def test_legacy_offset_success_cannot_hide_newer_utc_failures(tmp_path):
    store = SourceHealthStore(tmp_path / 'h.db')
    store.record('u', 'mail', ok=True, checked_at='2026-10-07T09:00:00+00:00')
    with store.db:
        store.db.execute("UPDATE source_health SET last_attempt_at='2026-10-07T14:30:00+05:30',last_success_at='2026-10-07T14:30:00+05:30'")
        store.db.execute("UPDATE source_health_checks SET checked_at='2026-10-07T14:30:00+05:30'")
    for minute in [1, 2, 3]:
        store.record('u', 'mail', ok=False, error='timeout', checked_at=f'2026-10-07T10:0{minute}:00+00:00')
    row = store.status('u', 'mail', now=datetime(2026, 10, 7, 10, 5, tzinfo=timezone.utc))
    assert row['status'] == 'down'
    assert row['last_error'] == 'timeout'
