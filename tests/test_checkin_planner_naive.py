"""Planning must not convert an ambiguous host-local clock into a valid due instant."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from test_token_audit_backends import persistence  # noqa: F401

from meemee.companion.checkins import CheckInScheduler, next_due
from meemee.companion.models import CheckInPreferences, UserProfile


def test_next_due_rejects_naive_caller_clock():
    with pytest.raises(ValueError, match='timezone'):
        next_due(CheckInPreferences(enabled=True), ZoneInfo('UTC'), datetime(2026, 10, 8, 9))  # noqa: DTZ001


def test_planner_rejects_naive_clock_without_enqueuing(persistence):  # noqa: F811
    store = persistence.companion
    store.upsert_user(UserProfile(user_id='owner', display_name='Owner', checkins=CheckInPreferences(enabled=True)))
    with pytest.raises(ValueError, match='timezone'):
        CheckInScheduler(store).plan_user('owner', datetime(2026, 10, 8, 9))  # noqa: DTZ001
    assert store.list_checkins('owner') == []
