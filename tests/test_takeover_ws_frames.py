"""Takeover websocket frame validation and task ownership (area 211)."""
import asyncio
import json
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from meemee.browser_api import build_browser_router
from meemee.browser_sessions import BrowserSessionError, BrowserSessionManager, BrowserSessionStore

TID, TOKEN = "bt_" + "a" * 32, "t" * 24


class FakeManager:
    def __init__(self, frame_error=None):
        self.frame_error, self.released, self.inputs = frame_error, [], []

    async def claim(self, takeover_id, token):
        if (takeover_id, token) != (TID, TOKEN):
            raise BrowserSessionError("invalid takeover link")
        return {"session_id": "s1"}

    async def frame(self, takeover_id, token, quality=60):
        if self.frame_error:
            raise self.frame_error
        return {"type": "frame", "jpeg": "", "url": "http://x"}

    async def human_input(self, takeover_id, token, event):
        self.inputs.append(event)
        return {"ok": True}

    async def release(self, takeover_id, token, note=None, outcome="completed"):
        if outcome not in {"completed", "declined"}:
            raise BrowserSessionError("outcome must be completed or declined")
        self.released.append((note, outcome))
        return {"session_id": "s1", "state": "agent", "outcome": outcome}


def _client(manager):
    auth = types.SimpleNamespace(dependency=lambda scope: (lambda: None))
    app = FastAPI()
    app.include_router(build_browser_router(manager, auth, frame_interval=0.05))
    return TestClient(app, raise_server_exceptions=True)


def _claim(ws):
    ws.send_text(json.dumps({"takeover_id": TID, "token": TOKEN}))
    assert ws.receive_json()["type"] == "claimed"


def _next_non_frame(ws):
    while True:
        message = ws.receive_json()
        if message["type"] != "frame":
            return message


# ---- 1. pre-auth handshake: malformed first frame must be rejected, not crash ----

@pytest.mark.parametrize("raw", ["[1,2]", '"text"', "12", "null", "not json", "true"])
def test_handshake_non_object_text_gets_error_and_4403(raw):
    client = _client(FakeManager())
    with client.websocket_connect("/v1/browser/takeover/ws") as ws:
        ws.send_text(raw)
        assert ws.receive_json()["type"] == "error"
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 4403


def test_handshake_binary_frame_gets_error_and_4403():
    client = _client(FakeManager())
    with client.websocket_connect("/v1/browser/takeover/ws") as ws:
        ws.send_bytes(b"\x00\x01")
        assert ws.receive_json()["type"] == "error"
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 4403


# ---- 2/3. established session: bad frames get an error frame; session stays usable ----

@pytest.mark.parametrize("raw", ["[1]", '"x"', "7", "null", "not json"])
def test_loop_non_object_text_gets_error_and_session_survives(raw):
    manager = FakeManager()
    with _client(manager).websocket_connect("/v1/browser/takeover/ws") as ws:
        _claim(ws)
        ws.send_text(raw)
        assert _next_non_frame(ws)["type"] == "error"
        ws.send_text(json.dumps({"type": "release", "outcome": "completed"}))
        assert _next_non_frame(ws)["type"] == "released"
    assert manager.released == [(None, "completed")]


def test_loop_binary_frame_gets_error_and_session_survives():
    manager = FakeManager()
    with _client(manager).websocket_connect("/v1/browser/takeover/ws") as ws:
        _claim(ws)
        ws.send_bytes(b"\xff")
        assert _next_non_frame(ws)["type"] == "error"
        ws.send_text(json.dumps({"type": "release"}))
        assert _next_non_frame(ws)["type"] == "released"


# ---- 5. release outcome / note come from the client ----

@pytest.mark.parametrize("outcome", ["bogus", ["x"], {"a": 1}, 5])
def test_release_bad_outcome_gets_error_and_does_not_end_session(outcome):
    manager = FakeManager()
    with _client(manager).websocket_connect("/v1/browser/takeover/ws") as ws:
        _claim(ws)
        ws.send_text(json.dumps({"type": "release", "outcome": outcome}))
        assert _next_non_frame(ws)["type"] == "error"
        ws.send_text(json.dumps({"type": "release", "outcome": "declined", "note": "n"}))
        assert _next_non_frame(ws)["type"] == "released"
    assert manager.released == [("n", "declined")]


@pytest.mark.parametrize("note", [5, ["x"], {"a": 1}])
def test_release_bad_note_gets_error_and_does_not_end_session(note):
    manager = FakeManager()
    with _client(manager).websocket_connect("/v1/browser/takeover/ws") as ws:
        _claim(ws)
        ws.send_text(json.dumps({"type": "release", "note": note}))
        assert _next_non_frame(ws)["type"] == "error"
        ws.send_text(json.dumps({"type": "release"}))
        assert _next_non_frame(ws)["type"] == "released"


# ---- valid behaviour unchanged ----

def test_valid_input_and_release_unchanged():
    manager = FakeManager()
    with _client(manager).websocket_connect("/v1/browser/takeover/ws") as ws:
        _claim(ws)
        ws.send_text(json.dumps({"type": "click", "x": 1, "y": 2}))
        assert _next_non_frame(ws) == {"type": "ack", "ok": True}
        ws.send_text(json.dumps({"type": "release", "note": "done", "outcome": "completed"}))
        released = _next_non_frame(ws)
        assert released == {"type": "released", "session_id": "s1", "state": "agent",
                            "outcome": "completed"}
    assert manager.inputs == [{"type": "click", "x": 1, "y": 2}]
    assert manager.released == [("done", "completed")]


# ---- 4. streamer task failure must be retrieved and reported, not left dangling ----

def test_streamer_failure_is_retrieved_and_logged(caplog):
    manager = FakeManager(frame_error=ZeroDivisionError("stream bug"))
    loop_errors = []
    with _client(manager).websocket_connect("/v1/browser/takeover/ws") as ws:
        _claim(ws)
        message = ws.receive_json()
        assert message["type"] == "error"
    del loop_errors
    assert any("stream bug" in r.getMessage() or (r.exc_info and "stream bug" in str(r.exc_info[1]))
               for r in caplog.records)


# ---- manager input numerics: bad numbers are BrowserSessionError, never ValueError/TypeError ----

@pytest.fixture
def manager_and_live(tmp_path):
    manager = BrowserSessionManager(
        BrowserSessionStore(tmp_path / "bs.sqlite3"), allow_private_hosts=True,
        public_url="https://meemee.example", takeover_ttl_seconds=60)

    class Mouse:
        async def click(self, *a, **k): pass
        async def move(self, *a, **k): pass
        async def down(self): pass
        async def up(self): pass
        async def wheel(self, *a): pass

    live = types.SimpleNamespace(
        id="s1", page=types.SimpleNamespace(mouse=Mouse(), url="http://x"),
        lock=asyncio.Lock(), last_activity=0, takeover_expires=None, allowed_domains=[])
    yield manager, live
    manager.store.db.close()


BAD_EVENTS = [
    {"type": "click", "x": "abc", "y": 1},
    {"type": "click", "x": None, "y": 1},
    {"type": "click", "x": [1], "y": 1},
    {"type": "drag", "from": ["a", 1], "to": [1, 1]},
    {"type": "drag", "from": [1, 1], "to": [None, 1]},
    {"type": "drag", "from": [1, 1], "to": [2, 2], "steps": "many"},
    {"type": "drag", "from": [1, 1], "to": [2, 2], "steps": None},
    {"type": "scroll", "dx": "x"},
    {"type": "scroll", "dy": None},
    {"type": "scroll", "dy": float("nan")},
    {"type": "scroll", "dy": float("inf")},
]


@pytest.mark.parametrize("event", BAD_EVENTS, ids=[json.dumps(e, default=str) for e in BAD_EVENTS])
def test_human_input_bad_numbers_raise_browser_session_error(manager_and_live, event):
    manager, live = manager_and_live
    with pytest.raises(BrowserSessionError):
        asyncio.run(manager._human_input(live, event))


# ---- decoder resource limits: deep nesting and huge integers are bad frames ----

HOSTILE = {
    "nested_arrays": "[" * 1500 + "0" + "]" * 1500,
    "nested_objects": '{"a":' * 1500 + "0" + "}" * 1500,
    "huge_integer": '{"type":"click","x":' + "9" * 5000 + "}",
}


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_handshake_hostile_decoder_input_gets_error_and_4403(name):
    with _client(FakeManager()).websocket_connect("/v1/browser/takeover/ws") as ws:
        ws.send_text(HOSTILE[name])
        assert ws.receive_json()["type"] == "error"
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
        assert closed.value.code == 4403


@pytest.mark.parametrize("name", sorted(HOSTILE))
def test_established_session_survives_hostile_decoder_input(name):
    manager = FakeManager()
    with _client(manager).websocket_connect("/v1/browser/takeover/ws") as ws:
        _claim(ws)
        ws.send_text(HOSTILE[name])
        assert _next_non_frame(ws)["type"] == "error"
        ws.send_text(json.dumps({"type": "release"}))
        assert _next_non_frame(ws)["type"] == "released"
    assert manager.released == [(None, "completed")]
