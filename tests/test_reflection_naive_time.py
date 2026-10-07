"""A persisted naive reflection clock must not poison later due passes."""

from datetime import datetime, timedelta, timezone

import pytest
from test_monitors_reflection_backends import stores  # noqa: F401


@pytest.mark.parametrize('operation', ['record', 'due'])
def test_reflection_rejects_naive_clock_before_state_change(stores, operation):  # noqa: F811
    _, schedule = stores()
    naive = datetime(2026, 10, 8, 9)  # noqa: DTZ001
    with pytest.raises(ValueError, match='timezone'):
        if operation == 'record':
            schedule.record('owner', 'ok', {}, 1, naive)
        else:
            schedule.due({'owner': 1}, timedelta(hours=1), naive)
    assert schedule.state('owner') is None
    assert schedule.due({'owner': 1}, timedelta(hours=1), datetime(2026, 10, 8, 10, tzinfo=timezone.utc)) == ['owner']


def test_reflection_aware_offset_remains_an_instant(stores):  # noqa: F811
    _, schedule = stores()
    instant = datetime(2026, 10, 8, 9, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    schedule.record('owner', 'ok', {}, 1, instant)
    assert schedule.due({'owner': 2}, timedelta(hours=1), datetime(2026, 10, 8, 4, tzinfo=timezone.utc)) == []
    assert schedule.due({'owner': 2}, timedelta(hours=1), datetime(2026, 10, 8, 5, tzinfo=timezone.utc)) == ['owner']
