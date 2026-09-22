from meemee.runs import RunStore
from meemee.types import RunReport


def report(ident): return RunReport(run_id=ident,goal="goal",final="done",steps_used=1,tool_results=[])


def test_run_history_owner_isolation_and_roundtrip(tmp_path):
    store=RunStore(tmp_path/"r.db"); store.add("a",report("r1")); store.add("b",report("r2"))
    assert store.get("a","r1")["final"]=="done"
    assert store.get("b","r1") is None
    assert [r["run_id"] for r in store.list("a")[0]]==["r1"]


def test_run_api_history_is_owner_scoped(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from meemee import api
    monkeypatch.setattr(api,"runs",RunStore(tmp_path/"api-runs.db"))
    api.runs.add("bootstrap",report("mine")); api.runs.add("other",report("theirs"))
    client=TestClient(api.app); headers={"Authorization":"Bearer test-bootstrap-token"}
    listed=client.get("/v1/runs",headers=headers)
    assert [run["run_id"] for run in listed.json()["runs"]]==["mine"]
    assert client.get("/v1/runs/mine",headers=headers).status_code==200
    assert client.get("/v1/runs/theirs",headers=headers).status_code==404
