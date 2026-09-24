import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("playwright")

from meemee.browser_sessions import (
    BrowserSessionError,
    BrowserSessionManager,
    BrowserSessionStore,
    check_url,
    detect_challenge,
)

CHALLENGE_PAGE = b"""<!doctype html><html><head><title>Gate</title></head><body>
<h1 id="msg">Please verify you are human</h1>
<input id="name" style="position:absolute;left:100px;top:200px;width:200px;height:30px">
<button id="solve" style="position:absolute;left:100px;top:300px;width:200px;height:60px"
 onclick="document.getElementById('msg').textContent='Welcome inside, '+document.getElementById('name').value">I am here</button>
</body></html>"""
SLIDER_PAGE = b"""<!doctype html><html><body><p id="out">locked</p>
<input id="slide" type="range" min="0" max="100" value="0" style="position:absolute;left:100px;top:100px;width:400px;margin:0"
 oninput="document.getElementById('out').textContent = this.value >= 90 ? 'unlocked' : 'locked'">
</body></html>"""
PLAIN_PAGE = b"<!doctype html><html><head><title>Plain</title></head><body><p id='p'>hello plain</p><a id='next' href='/challenge'>next</a></body></html>"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = CHALLENGE_PAGE if self.path.startswith("/challenge") else SLIDER_PAGE if self.path.startswith("/slider") else PLAIN_PAGE
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def manager(tmp_path):
    m = BrowserSessionManager(BrowserSessionStore(tmp_path / "bs.sqlite3"), allow_private_hosts=True,
                              public_url="https://meemee.example", takeover_ttl_seconds=60)
    yield m
    m.shutdown()


def run(coro):
    return asyncio.run(coro)


def test_detect_challenge_markers_and_frames():
    assert detect_challenge("Please VERIFY you are human")["detected"]
    assert detect_challenge("nothing", ["https://www.google.com/recaptcha/api2/anchor"])["frames"]
    assert not detect_challenge("regular page about robots")["detected"]


def test_check_url_policy(monkeypatch):
    with pytest.raises(BrowserSessionError, match="http or https"):
        check_url("file:///etc/passwd", [], True)
    with pytest.raises(BrowserSessionError, match="domain policy"):
        check_url("https://evil.example/x", ["good.example"], True)
    check_url("https://a.good.example/x", ["good.example"], True)
    monkeypatch.setattr("socket.getaddrinfo", lambda *a: [(2, 1, 6, "", ("10.0.0.5", 443))])
    with pytest.raises(BrowserSessionError, match="private"):
        check_url("https://internal.example", [], False)


def test_challenge_handoff_human_solves_and_agent_resumes(manager, site):
    state = run(manager.open(site + "/challenge"))
    assert state["state"] == "awaiting_human"
    assert state["challenge"]["detected"]
    takeover = state["takeover"]
    assert takeover["url"].startswith("https://meemee.example/browser/takeover#" + takeover["takeover_id"] + ".")
    sid = state["session_id"]

    with pytest.raises(BrowserSessionError, match="agent cannot act"):
        run(manager.act(sid, [{"kind": "click", "selector": "#solve"}]))
    with pytest.raises(BrowserSessionError, match="invalid takeover link"):
        run(manager.claim(takeover["takeover_id"], "wrong-token-wrong-token"))
    with pytest.raises(BrowserSessionError, match="claim the takeover"):
        run(manager.human_input(takeover["takeover_id"], takeover["token"], {"type": "click", "x": 10, "y": 10}))

    claimed = run(manager.claim(takeover["takeover_id"], takeover["token"]))
    assert claimed["session_id"] == sid and claimed["viewport"] == {"width": 1280, "height": 800}
    frame = run(manager.frame(takeover["takeover_id"], takeover["token"]))
    assert frame["jpeg"].startswith("/9j/")  # JPEG magic, base64

    tid, tok = takeover["takeover_id"], takeover["token"]
    run(manager.human_input(tid, tok, {"type": "click", "x": 150, "y": 215}))
    run(manager.human_input(tid, tok, {"type": "type", "text": "secret-Udita"}))
    run(manager.human_input(tid, tok, {"type": "click", "x": 150, "y": 330}))
    with pytest.raises(BrowserSessionError, match="outside viewport"):
        run(manager.human_input(tid, tok, {"type": "click", "x": 5000, "y": 10}))
    with pytest.raises(BrowserSessionError, match="key not allowed"):
        run(manager.human_input(tid, tok, {"type": "key", "key": "F12"}))
    run(manager.release(tid, tok, "solved it"))

    resumed = run(manager.wait_for_human(sid, 5))
    assert resumed["state"] == "agent"
    assert resumed["takeover_outcome"] == "completed"
    assert resumed["note"] == "solved it"
    assert "Welcome inside, secret-Udita" in resumed["text"]

    # same page, agent back in control
    after = run(manager.act(sid, [{"kind": "fill", "selector": "#name", "value": "x"}], auto_takeover=False))
    assert after["state"] == "agent"
    with pytest.raises(BrowserSessionError, match="already ended"):
        run(manager.claim(tid, tok))

    events = manager.store.events(sid)
    kinds = [e["kind"] for e in events]
    assert kinds[:2] == ["opened", "takeover_requested"]
    assert "takeover_claimed" in kinds and "takeover_released" in kinds
    assert "secret-Udita" not in json.dumps(events)
    typed = next(e for e in events if e["kind"] == "human_type")
    assert typed["detail"] == {"chars": 12}
    assert run(manager.close(sid))["state"] == "closed"
    with pytest.raises(BrowserSessionError, match="closed"):
        run(manager.snapshot(sid))


def test_agent_requested_takeover_expires_and_returns_control(tmp_path, site):
    m = BrowserSessionManager(BrowserSessionStore(tmp_path / "bs.sqlite3"), allow_private_hosts=True, takeover_ttl_seconds=1)
    try:
        state = run(m.open(site + "/plain"))
        assert state["state"] == "agent" and "hello plain" in state["text"]
        takeover = run(m.request_takeover(state["session_id"], "please log in"))
        result = run(m.wait_for_human(state["session_id"], 10))
        assert result["state"] == "agent"
        assert result["takeover_outcome"] == "expired"
        with pytest.raises(BrowserSessionError):
            run(m.claim(takeover["takeover_id"], takeover["token"]))
    finally:
        m.shutdown()


def test_domain_policy_applies_to_human_navigation(manager, site):
    state = run(manager.open(site + "/plain", allowed_domains=["127.0.0.1"]))
    t = run(manager.request_takeover(state["session_id"], "check"))
    run(manager.claim(t["takeover_id"], t["token"]))
    with pytest.raises(BrowserSessionError, match="domain policy"):
        run(manager.human_input(t["takeover_id"], t["token"], {"type": "goto", "url": "https://example.com/"}))


def test_session_limit_and_restart_marks_lost(tmp_path, site):
    store_path = tmp_path / "bs.sqlite3"
    m = BrowserSessionManager(BrowserSessionStore(store_path), allow_private_hosts=True, max_sessions=1)
    try:
        first = run(m.open(site + "/plain"))
        with pytest.raises(BrowserSessionError, match="limit"):
            run(m.open(site + "/plain"))
    finally:
        m._live.clear()  # simulate a crash: live pages vanish without a clean close
        m.shutdown()
    reopened = BrowserSessionStore(store_path)
    BrowserSessionManager(reopened)
    record = reopened.get_session(first["session_id"])
    assert record["state"] == "lost" and record["close_reason"] == "process_restart"


def test_agent_tools_hide_token_but_return_link(manager, site):
    from meemee.tools import ToolRegistry
    from meemee.tools.browser_session import session_tools

    registry = ToolRegistry()
    for tool in session_tools(manager):
        registry.register(tool)
    result = run(registry.execute("browser.session_open", {"url": site + "/challenge"}))
    assert result.ok, result.error
    content = result.content
    assert content["state"] == "awaiting_human"
    assert "token" not in content["takeover"]
    assert "#" + content["takeover"]["takeover_id"] + "." in content["takeover"]["url"]
    waited = run(registry.execute("browser.session_wait_human", {"session_id": content["session_id"], "timeout_seconds": 1}))
    assert waited.ok and waited.content["takeover_outcome"] == "pending"
    closed = run(registry.execute("browser.session_close", {"session_id": content["session_id"]}))
    assert closed.content["state"] == "closed"


def test_api_websocket_takeover_end_to_end(site):
    import os
    os.environ.setdefault("MEEMEE_API_TOKEN", "test-bootstrap-token")
    from fastapi.testclient import TestClient

    from meemee import api

    client = TestClient(api.app)
    headers = {"Authorization": f"Bearer {os.environ['MEEMEE_API_TOKEN']}"}
    manager = api.browser_sessions
    original = manager.allow_private_hosts
    manager.allow_private_hosts = True
    try:
        opened = client.post("/v1/browser/sessions", headers=headers, json={"url": site + "/challenge"})
        assert opened.status_code == 201, opened.text
        state = opened.json()
        assert state["state"] == "awaiting_human"
        assert client.post("/v1/browser/sessions", headers=headers, json={"url": "file:///etc/passwd"}).status_code == 422
        sid = state["session_id"]
        t = state["takeover"]
        page = client.get("/browser/takeover")
        assert page.status_code == 200 and "connect-src 'self'" in page.headers["content-security-policy"]
        assert client.get("/v1/browser/sessions").status_code == 401
        listed = client.get("/v1/browser/sessions", headers=headers).json()["sessions"]
        assert any(s["id"] == sid for s in listed)

        with client.websocket_connect("/v1/browser/takeover/ws") as ws:
            ws.send_text(json.dumps({"takeover_id": t["takeover_id"], "token": "nope-nope-nope-nope"}))
            assert ws.receive_json()["type"] == "error"

        with client.websocket_connect("/v1/browser/takeover/ws") as ws:
            ws.send_text(json.dumps({"takeover_id": t["takeover_id"], "token": t["token"]}))
            assert ws.receive_json()["type"] == "claimed"
            seen = set()
            ws.send_text(json.dumps({"type": "click", "x": 150, "y": 330}))
            ws.send_text(json.dumps({"type": "release", "outcome": "completed", "note": "done"}))
            while True:
                message = ws.receive_json()
                seen.add(message["type"])
                if message["type"] == "released":
                    break
            assert "frame" in seen or "ack" in seen

        detail = client.get(f"/v1/browser/sessions/{sid}", headers=headers).json()
        assert detail["session"]["state"] == "agent"
        assert detail["takeovers"][-1]["outcome"] == "completed"
        snap = run(manager.snapshot(sid))
        assert "Welcome inside" in snap["text"]

        assert client.put("/v1/companion/users/browser-notify", headers=headers, json={"display_name": "Notify"}).status_code == 200
        notified = client.post("/v1/browser/sessions", headers=headers, json={"url": site + "/challenge", "notify_user_id": "browser-notify"}).json()
        notices = client.get("/v1/browser/notices", headers=headers, params={"session_id": notified["session_id"]}).json()["notices"]
        assert notices[0]["status"] == "delivered" and notices[0]["channel"] == "local"
        assert client.post("/v1/browser/notices/tick", headers=headers).status_code == 200
        client.delete(f"/v1/browser/sessions/{notified['session_id']}", headers=headers)

        again = client.post(f"/v1/browser/sessions/{sid}/takeover", headers=headers, json={"reason": "operator check"})
        assert again.status_code == 201 and again.json()["token"]
        assert client.delete(f"/v1/browser/sessions/{sid}", headers=headers).json()["state"] == "closed"
    finally:
        manager.allow_private_hosts = original


def test_human_drag_moves_slider(manager, site):
    state = run(manager.open(site + "/slider"))
    t = run(manager.request_takeover(state["session_id"], "slide to unlock"))
    run(manager.claim(t["takeover_id"], t["token"]))
    with pytest.raises(BrowserSessionError, match="outside viewport"):
        run(manager.human_input(t["takeover_id"], t["token"], {"type": "drag", "from": [105, 108], "to": [9000, 108]}))
    run(manager.human_input(t["takeover_id"], t["token"], {"type": "drag", "from": [105, 108], "to": [499, 108]}))
    run(manager.release(t["takeover_id"], t["token"]))
    assert "unlocked" in run(manager.wait_for_human(state["session_id"], 5))["text"]
    drags = [e for e in manager.store.events(state["session_id"]) if e["kind"] == "human_drag"]
    assert drags and drags[0]["detail"]["to"] == [499, 108]


def test_takeover_notice_queued_delivered_locally_and_link_erased(tmp_path, site):
    from meemee.browser_notices import TakeoverNoticeQueue
    from meemee.companion.channels import LocalChannel
    from meemee.companion.models import UserProfile
    from meemee.companion.store import CompanionStore

    companion = CompanionStore(tmp_path / "companion.sqlite3")
    companion.upsert_user(UserProfile(user_id="udita", display_name="Udita"))
    queue = TakeoverNoticeQueue(tmp_path / "notices.sqlite3")
    m = BrowserSessionManager(BrowserSessionStore(tmp_path / "bs.sqlite3"), allow_private_hosts=True,
                              public_url="https://meemee.example", notices=queue)
    m.notice_delivery = lambda: queue.deliver_pending(m.store, companion, {"local": LocalChannel(companion)})
    try:
        state = run(m.open(site + "/challenge", notify_user_id="udita"))
        assert state["takeover"]["notice_queued_for"] == "udita"
        notices = queue.list(state["session_id"])
        assert [n["status"] for n in notices] == ["delivered"] and notices[0]["channel"] == "local"
        conversation = companion.latest_conversation("udita", "local")
        message = companion.history(conversation["id"])[-1]["content"]
        assert state["takeover"]["url"] in message and "needs a hand" in message
        stored = queue.db.execute("SELECT text FROM browser_takeover_notices").fetchone()[0]
        assert stored is None  # link with token erased after delivery

        # a notice for a takeover that ended before delivery is cancelled, not sent
        m.notice_delivery = None
        t = run(m.request_takeover(state["session_id"], "second look"))
        run(m.claim(t["takeover_id"], t["token"]))
        run(m.release(t["takeover_id"], t["token"]))
        result = run(queue.deliver_pending(m.store, companion, {"local": LocalChannel(companion)}))
        assert result == [{"id": result[0]["id"], "status": "cancelled"}]

        # unknown user fails without sending
        other = run(m.open(site + "/challenge", notify_user_id="nobody"))
        assert run(queue.deliver_pending(m.store, companion, {}))[0]["status"] == "failed"
        assert other["takeover"]["notice_queued_for"] == "nobody"
    finally:
        m.shutdown()
