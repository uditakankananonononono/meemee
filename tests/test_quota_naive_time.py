"""Daily quota clocks must identify the same instant on every host."""

from datetime import datetime

import pytest
from test_token_audit_backends import persistence  # noqa: F401


@pytest.mark.parametrize('method', ['consume_job', 'status'])
def test_quota_rejects_naive_clock_before_effects(persistence, method):  # noqa: F811
    quota = persistence.quotas
    with pytest.raises(ValueError, match='timezone'):
        getattr(quota, method)('owner', datetime(2026, 10, 8, 0, 30))  # noqa: DTZ001
    assert quota.status('owner')['used'] == 0


def test_quota_aware_clock_uses_utc_day(persistence):  # noqa: F811
    from datetime import timedelta, timezone

    quota = persistence.quotas
    instant = datetime(2026, 10, 8, 0, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    quota.consume_job('owner', instant)
    assert quota.status('owner', instant)['day'] == '2026-10-07'
    assert quota.status('owner', datetime(2026, 10, 7, 19, tzinfo=timezone.utc))['used'] == 1
