"""Daily job quotas and plan assignments: one contract, SQLite and PostgreSQL."""
import threading
from datetime import datetime, timedelta, timezone

import pytest
from test_token_audit_backends import persistence  # noqa: F401 - shared two-backend fixture

from meemee.quotas import QuotaExceeded
from meemee_persist_pg.interfaces import EntitlementStoreInterface, QuotaStoreInterface

NOON = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)


def test_stores_match_interfaces(persistence):  # noqa: F811
    assert isinstance(persistence.quotas, QuotaStoreInterface) and persistence.quotas.ping()
    assert isinstance(persistence.entitlements, EntitlementStoreInterface) and persistence.entitlements.ping()


def test_quota_counts_to_the_limit_then_refuses_without_counting(persistence):  # noqa: F811
    q = persistence.quotas
    assert q.limit("p") == 100
    q.set_limit("p", 3)
    assert [q.consume_job("p", NOON)["remaining"] for _ in range(3)] == [2, 1, 0]
    with pytest.raises(QuotaExceeded, match=r"\(3\)"):
        q.consume_job("p", NOON)
    assert q.status("p", NOON) == {"day": "2026-09-24", "used": 3, "limit": 3, "remaining": 0}
    q.set_limit("p", 4)  # raising the limit mid-day allows exactly one more
    assert q.consume_job("p", NOON)["used"] == 4
    with pytest.raises(ValueError):
        q.set_limit("p", 0)


def test_quota_days_are_utc(persistence):  # noqa: F811
    q = persistence.quotas
    q.set_limit("d", 1)
    late_ist = datetime(2026, 9, 25, 2, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))  # 20:30 UTC on the 24th
    assert q.consume_job("d", late_ist)["day"] == "2026-09-24"
    with pytest.raises(QuotaExceeded):
        q.consume_job("d", NOON)
    assert q.consume_job("d", NOON + timedelta(days=1))["day"] == "2026-09-25"
    assert q.status("other", NOON)["used"] == 0


def test_concurrent_submissions_never_exceed_the_limit(persistence):  # noqa: F811
    q = persistence.quotas
    q.set_limit("race", 10)
    ok, refused, barrier = [], [], threading.Barrier(30)

    def submit():
        barrier.wait()
        try:
            q.consume_job("race", NOON); ok.append(1)
        except QuotaExceeded:
            refused.append(1)

    threads = [threading.Thread(target=submit) for _ in range(30)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert (len(ok), len(refused)) == (10, 20)
    assert q.status("race", NOON)["used"] == 10


def test_quota_delete_principal(persistence):  # noqa: F811
    q = persistence.quotas
    q.set_limit("gone", 5); q.consume_job("gone", NOON); q.consume_job("gone", NOON + timedelta(days=1))
    assert q.delete_principal("gone") == {"quota_usage": 2, "quota_limits": 1}
    assert q.status("gone", NOON)["used"] == 0 and q.limit("gone") == 100


def test_entitlements_default_assign_allows_and_delete(persistence):  # noqa: F811
    e = persistence.entitlements
    assert e.get("x") == {"principal": "x", "plan": "starter", "limits": {"daily_jobs": 100, "webhooks": 3, "persistent_approvals": 10}}
    assert e.allows("x", "webhooks", 2) and not e.allows("x", "webhooks", 3)
    assert e.assign("x", "team", "2026-09-24T12:00:00+00:00")["plan"] == "team"
    assert e.plan_name("x") == "team" and e.allows("x", "webhooks", 24)
    assert e.assign("x", "business", "2026-09-24T13:00:00+00:00")["limits"]["daily_jobs"] == 10_000
    with pytest.raises(ValueError, match="unknown plan"):
        e.assign("x", "enterprise", "2026-09-24T13:00:00+00:00")
    assert e.delete_principal("x") == 1 and e.plan_name("x") == "starter"
    assert e.delete_principal("x") == 0


def test_defaults_come_from_settings(tmp_path):
    from meemee.config import Settings
    from meemee.persistence import persistence_from_settings

    p = persistence_from_settings(Settings(_env_file=None, data_dir=tmp_path, default_daily_jobs=7, default_plan="team"))
    assert p.quotas.limit("n") == 7 and p.entitlements.plan_name("n") == "team"


def test_account_purger_uses_the_shared_stores(persistence, tmp_path):  # noqa: F811
    from meemee.account_deletion import build_account_purger
    from meemee.config import Settings

    purger = build_account_purger(Settings(_env_file=None, data_dir=tmp_path / "purge"), persistence)
    assert purger.targets.quotas is persistence.quotas and purger.targets.entitlements is persistence.entitlements


def test_account_export_and_import_in_postgresql_mode_never_touch_local_files(monkeypatch, tmp_path):
    """PostgreSQL mode exports/imports the shared rows (tests_pg/test_operator_tools_pg.py and
    tests/test_operator_tools_multihost_e2e.py); without a DSN it stops before any local read or write."""
    from typer.testing import CliRunner

    from meemee.cli import app

    monkeypatch.setenv("MEEMEE_PERSISTENCE_BACKEND", "postgresql")
    monkeypatch.setenv("MEEMEE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MEEMEE_POSTGRES_DSN", raising=False)
    runner = CliRunner()
    for args in (["account-export", "p", str(tmp_path / "out.json")], ["account-import", str(tmp_path / "in.json")]):
        result = runner.invoke(app, args)
        assert result.exit_code == 2 and "MEEMEE_POSTGRES_DSN" in result.output
    assert sorted(p.name for p in tmp_path.iterdir()) == []
