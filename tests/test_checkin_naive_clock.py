"""Check-in clocks require explicit zones instead of host-local interpretation."""

from datetime import datetime

import pytest
from test_companion_backends import backend, store  # noqa: F401


@pytest.mark.parametrize('operation', ['schedule', 'claim'])
def test_checkin_rejects_naive_clock_without_state_change(store, operation):  # noqa: F811
    moment = datetime(2026, 10, 8, 9)  # noqa: DTZ001 - deliberate invalid clock
    with pytest.raises(ValueError, match='timezone'):
        if operation == 'schedule':
            store.schedule_checkin('owner', moment, 'slot', 'local', None)
        else:
            store.claim_checkin(moment)
    assert store.list_checkins('owner') == []
