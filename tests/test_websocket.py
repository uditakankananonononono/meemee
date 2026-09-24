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
    # The handler accepts first and then closes with the reason code, so real
    # network clients see 44xx instead of a bare HTTP 403 handshake refusal.
    for headers, code in (({},4401),({"Authorization":"Bearer test-bootstrap-token"},4404)):
        try:
            with client.websocket_connect(f"/v1/jobs/{ident}/ws",headers=headers) as socket:
                socket.receive_json()
        except WebSocketDisconnect as exc: assert exc.code==code
        else: raise AssertionError("connection should be rejected")


def test_websocket_rejects_missing_scope_and_bad_cursor(monkeypatch,tmp_path):
    from starlette.websockets import WebSocketDisconnect
    store=JobStore(tmp_path/"jobs.db"); monkeypatch.setattr(api,"jobs",store)
    ident=store.enqueue("work",principal="bootstrap")
    from meemee.auth import TokenStore
    token_store=TokenStore(tmp_path/"auth.db"); monkeypatch.setattr(api,"tokens",token_store)
    _, token=token_store.create("ws-no-read",{"runs:write"})
    client=TestClient(api.app)
    cases=((f"/v1/jobs/{ident}/ws",f"Bearer {token}",4403),
           (f"/v1/jobs/{ident}/ws?after=x","Bearer test-bootstrap-token",4400))
    for path, auth, code in cases:
        try:
            with client.websocket_connect(path,headers={"Authorization":auth}) as socket:
                socket.receive_json()
        except WebSocketDisconnect as exc: assert exc.code==code
        else: raise AssertionError("connection should be rejected")
