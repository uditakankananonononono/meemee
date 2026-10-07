"""Expired worker leases cannot authorize goal outcome publication."""
import pytest

from meemee.goals import GoalConflict, GoalStore


def test_expired_worker_cannot_complete_before_reclaim(tmp_path):
    store = GoalStore(tmp_path / 'g.db')
    goal = store.create('owner', 'fixture goal')
    store.claim('owner', 'worker')
    store.db.execute("UPDATE agency_goals SET lease_until='2000-01-01T00:00:00+00:00'")
    with pytest.raises(GoalConflict, match='expired'):
        store.transition('owner', goal['id'], 'completed', worker_id='worker', outcome='stale publication')
    assert store.get('owner', goal['id'])['status'] == 'active'
