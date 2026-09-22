from datetime import datetime, timezone
from pathlib import Path

import pytest

from meemee.source_health import SourceHealthStore


def test_health_transitions_and_history(tmp_path: Path):
    health = SourceHealthStore(tmp_path / "h.db")
    assert health.status("u", "mail")["status"] == "unknown"
    row = health.record(
        "u", "mail", ok=True, latency_ms=12.5, cursor="c1", checked_at="2026-09-22T10:00:00+00:00"
    )
    assert row["total_successes"] == 1
    assert (
        health.status("u", "mail", now=datetime(2026, 9, 22, 10, 5, tzinfo=timezone.utc))["status"]
        == "healthy"
    )
    health.record("u", "mail", ok=False, error="timeout", checked_at="2026-09-22T10:06:00+00:00")
    assert (
        health.status("u", "mail", now=datetime(2026, 9, 22, 10, 7, tzinfo=timezone.utc))["status"]
        == "degraded"
    )
    health.record("u", "mail", ok=False, error="timeout", checked_at="2026-09-22T10:08:00+00:00")
    health.record("u", "mail", ok=False, error="timeout", checked_at="2026-09-22T10:09:00+00:00")
    assert (
        health.status("u", "mail", now=datetime(2026, 9, 22, 10, 10, tzinfo=timezone.utc))["status"]
        == "down"
    )
    assert len(health.history("u", "mail")) == 4


def test_health_validates_inputs_and_owner_scope(tmp_path: Path):
    health = SourceHealthStore(tmp_path / "h.db")
    with pytest.raises(ValueError):
        health.record("u", "mail", ok=True, error="bad")
    with pytest.raises(ValueError):
        health.record("u", "mail", ok=True, latency_ms=-1)
    health.record("u", "mail", ok=True)
    assert health.list_status("other") == []
