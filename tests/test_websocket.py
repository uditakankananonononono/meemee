from fastapi.testclient import TestClient

from meemee import api
from meemee.jobs import JobStore


def test_authenticated_owner_websocket_replays_events(monkeypatch,tmp_path):
    store=JobStore(tmp_path/"jobs.db"); monkeypatch.setattr(api,"jobs",store)
    ident=store.enqueue("work",principal="bootstrap")
    with TestClient(api.app).websocket_connect(f"/v1/jobs/{ident}/ws?after=0",headers={"Authorization":"Bearer test-bootstrap-token"}) as socket:
        event=socket.receive_json()
        assert event["sequence"]==1 and event["kind"]=="queued"


def test_websocket_rejects_missing_auth_and_other_owner(monkeypatch,tmp_path):
    from starlette.websockets import WebSocketDisconnect
    store=JobStore(tmp_path/"jobs.db"); monkeypatch.setattr(api,"jobs",store)
    ident=store.enqueue("private",principal="other")
    client=TestClient(api.app)
    for headers, code in (({},4401),({"Authorization":"Bearer test-bootstrap-token"},4404)):
        try:
            with client.websocket_connect(f"/v1/jobs/{ident}/ws",headers=headers): pass
        except WebSocketDisconnect as exc: assert exc.code==code
        else: raise AssertionError("connection should be rejected")
