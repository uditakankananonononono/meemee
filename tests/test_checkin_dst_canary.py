"""Cadence is elapsed time, not timezone wall-clock subtraction."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from meemee.companion.checkins import next_due
from meemee.companion.models import CheckInPreferences


@pytest.mark.parametrize('after', [datetime(2026,3,8,6,30,tzinfo=timezone.utc), datetime(2026,11,1,5,30,tzinfo=timezone.utc)])
def test_hour_cadence_crossing_dst_is_sixty_elapsed_minutes(after):
    prefs = CheckInPreferences(enabled=True, cadence_minutes=60)
    due = next_due(prefs, ZoneInfo('America/New_York'), after)
    assert due - after == timedelta(minutes=60)
