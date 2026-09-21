from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from meemee.quotas import QuotaExceeded, QuotaStore


def test_quota_consumption_and_atomic_rejection(tmp_path: Path):
    store = QuotaStore(tmp_path / "q.db", default_daily_jobs=2)
    assert store.consume_job("u")["remaining"] == 1
    assert store.consume_job("u")["remaining"] == 0
    with pytest.raises(QuotaExceeded): store.consume_job("u")
    assert store.status("u")["used"] == 2


def test_quota_custom_limit_and_utc_day_reset(tmp_path: Path):
    store = QuotaStore(tmp_path / "q.db", default_daily_jobs=1)
    store.set_limit("u", 3)
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert store.consume_job("u", now)["limit"] == 3
    assert store.status("u", now + timedelta(days=1))["used"] == 0
