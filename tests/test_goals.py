from datetime import datetime, timedelta, timezone

import pytest

from meemee.goals import GoalConflict, GoalStore


def test_dependencies_priorities_and_tenant_isolation(tmp_path):
    store=GoalStore(tmp_path/"goals.sqlite3")
    first=store.create("u","research",priority=1)
    second=store.create("u","ship",priority=100,depends_on=[first["id"]])
    store.create("other","private",priority=1000)
    claim=store.claim("u","worker")
    assert claim["id"]==first["id"] and claim["status"]=="active"
    with pytest.raises(KeyError): store.get("other",first["id"])
    done=store.transition("u",first["id"],"completed",worker_id="worker",outcome="evidence")
    assert done["outcome"]=="evidence"
    assert store.claim("u","worker")["id"]==second["id"]

def test_lease_ownership_versioning_and_recovery(tmp_path):
    store=GoalStore(tmp_path/"goals.sqlite3"); goal=store.create("u","work")
    claimed=store.claim("u","a",lease_seconds=60)
    with pytest.raises(GoalConflict): store.transition("u",goal["id"],"completed",worker_id="b")
    renewed=store.renew("u",goal["id"],"a",60)
    assert renewed["version"]==claimed["version"]+1
    with pytest.raises(GoalConflict): store.transition("u",goal["id"],"completed",worker_id="a",expected_version=1)
    store.db.execute("UPDATE agency_goals SET lease_until=? WHERE id=?",((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),goal["id"]))
    assert store.claim("u","b")["lease_owner"]=="b"

def test_invalid_transition_and_unfinished_dependency(tmp_path):
    store=GoalStore(tmp_path/"goals.sqlite3"); a=store.create("u","a"); b=store.create("u","b",depends_on=[a["id"]])
    with pytest.raises(GoalConflict): store.transition("u",b["id"],"completed")
    store.transition("u",a["id"],"cancelled")
    assert store.claim("u","w") is None
