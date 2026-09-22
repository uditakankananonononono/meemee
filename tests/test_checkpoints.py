import pytest

from meemee.checkpoints import CheckpointConflict, CheckpointStore


def test_checkpoint_history_resume_and_tenant_isolation(tmp_path):
    store=CheckpointStore(tmp_path/"checkpoints.sqlite3")
    store.save("u","run","goal","planned",{"steps":[1]},expected_sequence=0)
    two=store.save("u","run","goal","acting",{"cursor":2},expected_sequence=1)
    assert [x["sequence"] for x in store.history("u","run")]==[1,2]
    assert store.resume("u","run")=={"run_id":"run","goal_id":"goal","next_sequence":3,"phase":"acting","state":{"cursor":2},"checkpoint_id":two["id"]}
    assert store.latest("other","run") is None

def test_idempotent_save_returns_same_checkpoint_and_rejects_changed_content(tmp_path):
    store=CheckpointStore(tmp_path/"checkpoints.sqlite3")
    first=store.save("u","run","goal","tool",{"result":"ok"},idempotency_key="tool-1")
    replay=store.save("u","run","goal","tool",{"result":"ok"},idempotency_key="tool-1")
    assert replay["id"]==first["id"] and len(store.history("u","run"))==1
    with pytest.raises(CheckpointConflict): store.save("u","run","goal","tool",{"result":"changed"},idempotency_key="tool-1")

def test_optimistic_sequence_detects_competing_writer(tmp_path):
    store=CheckpointStore(tmp_path/"checkpoints.sqlite3")
    store.save("u","run","goal","start",{},expected_sequence=0)
    with pytest.raises(CheckpointConflict,match="expected 0, actual 1"):
        store.save("u","run","goal","again",{},expected_sequence=0)
    assert store.get("u","run",1)["state_sha256"]
    with pytest.raises(KeyError): store.resume("u","missing")
