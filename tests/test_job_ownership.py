
from meemee.jobs import JobStore


def test_job_owner_listing_and_isolation(tmp_path):
    store=JobStore(tmp_path/"j.db")
    a=store.enqueue("alpha",principal="a"); b=store.enqueue("beta",principal="b")
    assert [job["id"] for job in store.list_for_principal("a")[0]] == [a]
    assert store.get_owned(a,"a")["goal"]=="alpha"
    assert store.get_owned(a,"b") is None
    assert store.get_owned(b,"a") is None


def test_legacy_job_has_no_accidental_owner(tmp_path):
    store=JobStore(tmp_path/"j.db"); legacy=store.enqueue("legacy")
    assert store.get_owned(legacy,"bootstrap") is None
    assert store.list_for_principal("bootstrap")[0]==[]


def test_job_listing_status_and_cursor(tmp_path):
    store=JobStore(tmp_path/"j.db")
    first=store.enqueue("first",principal="u"); second=store.enqueue("second",principal="u")
    store.db.execute("UPDATE jobs SET updated_at=? WHERE id=?",("2026-01-01T00:00:00+00:00",first))
    assert [x["id"] for x in store.list_for_principal("u",limit=1)[0]]==[second]
    assert [x["id"] for x in store.list_for_principal("u",before="2026-02-01T00:00:00+00:00")[0]]==[first]


def test_api_job_reads_are_owner_scoped(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from meemee import api
    monkeypatch.setattr(api,"jobs",JobStore(tmp_path/"api-jobs.db"))
    own=api.jobs.enqueue("mine",principal="bootstrap")
    other=api.jobs.enqueue("theirs",principal="other")
    client=TestClient(api.app); headers={"Authorization":"Bearer test-bootstrap-token"}
    listed=client.get("/v1/jobs",headers=headers)
    assert listed.status_code==200 and [j["id"] for j in listed.json()["jobs"]]==[own]
    assert client.get(f"/v1/jobs/{own}",headers=headers).status_code==200
    assert client.get(f"/v1/jobs/{other}",headers=headers).status_code==404
    assert client.delete(f"/v1/jobs/{other}",headers=headers).status_code==404
    assert client.get(f"/v1/jobs/{other}/events",headers=headers).status_code==404
