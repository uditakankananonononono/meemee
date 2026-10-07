"""A retry budget is a positive integer, never a boolean or fractional count."""

from datetime import datetime, timezone

import pytest
from test_companion_backends import backend, store  # noqa: F401


@pytest.mark.parametrize('budget', [0, -1, True, 1.5])
def test_checkin_rejects_invalid_retry_budget(store, budget):  # noqa: F811
    with pytest.raises(ValueError, match='max_attempts'):
        store.schedule_checkin('owner', datetime.now(timezone.utc), 'slot', 'local', None, max_attempts=budget)
    assert store.list_checkins('owner') == []
