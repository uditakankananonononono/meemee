"""A future-dated success is not evidence of current source freshness."""
from datetime import datetime, timezone

from meemee.source_health import SourceHealthStore


def test_future_success_is_not_healthy(tmp_path):
    store = SourceHealthStore(tmp_path / 'h.db')
    store.record('u', 'mail', ok=True, checked_at='2026-10-08T10:00:00+00:00')
    row = store.status('u', 'mail', now=datetime(2026, 10, 7, 10, tzinfo=timezone.utc))
    assert row['status'] == 'unknown'
    assert row['reason'] == 'future_check_timestamp'
