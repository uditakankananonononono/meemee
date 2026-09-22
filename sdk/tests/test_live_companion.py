"""Live companion integration: the SDK against a real booted Meemee server.

The server under test is real end to end (HTTP, auth, stores, engine, delivery
queue). Only the external model endpoint is stubbed with a tiny local HTTP
responder, the same boundary the production config treats as external.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from meemee_client import MeemeeClient, PermissionDeniedError

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_PACKAGE = REPO_ROOT / "meemee"

pytestmark = pytest.mark.skipif(
    not (SERVER_PACKAGE / "api.py").exists(),
    reason="meemee server package not found next to sdk/",
)

REPLY_TEXT = "live companion reply"
FACT_TEXT = "Likes live integration tests"


class _ModelHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length).decode()
        if "Extract durable facts" in body:
            content = json.dumps({"facts": [{"category": "preference", "text": FACT_TEXT}]})
        else:
            content = REPLY_TEXT
        payload = json.dumps({"choices": [{"message": {"content": content}}]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args) -> None:
        pass


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def live_server(tmp_path_factory: pytest.TempPathFactory):
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        pytest.skip("server dependencies (fastapi, uvicorn) not installed here")
    model_server = ThreadingHTTPServer(("127.0.0.1", _free_port()), _ModelHandler)
    threading.Thread(target=model_server.serve_forever, daemon=True).start()
    port = _free_port()
    data_dir = tmp_path_factory.mktemp("meemee-companion-data")
    env = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT),
        "MEEMEE_API_TOKEN": "bootstrap-test-token",
        "MEEMEE_DATA_DIR": str(data_dir),
        "MEEMEE_VAULT_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        "MEEMEE_MODEL_BASE_URL": f"http://127.0.0.1:{model_server.server_port}/v1",
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "meemee.api:app", "--host", "127.0.0.1", "--port", str(port)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            output = proc.stdout.read() if proc.stdout else ""
            pytest.fail(f"server exited during startup:\n{output}")
        try:
            if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.25)
    else:
        proc.kill()
        pytest.fail("server did not become healthy within 20s")
    yield base
    proc.kill()
    proc.wait(timeout=10)
    model_server.shutdown()


@pytest.fixture(scope="module")
def admin(live_server: str) -> MeemeeClient:
    with MeemeeClient(live_server, auth="bootstrap-test-token") as client:
        yield client


def test_live_user_persona_and_facts(admin: MeemeeClient) -> None:
    from meemee_client import CheckInPreferences, PersonaConfig, QuietHours

    saved = admin.companion.upsert_user(
        "live-user", "Live User", timezone="Asia/Calcutta",
        persona=PersonaConfig(tone="blunt", style_rules=["no fluff"]),
        checkins=CheckInPreferences(
            enabled=True, cadence_minutes=15,
            quiet_hours=QuietHours(start="22:00", end="06:00"),
        ),
    )
    assert saved.persona.tone == "blunt"
    assert saved.checkins.quiet_hours.end == "06:00"
    fetched = admin.companion.get_user("live-user")
    assert fetched.timezone == "Asia/Calcutta"
    assert any(user.user_id == "live-user" for user in admin.companion.list_users())
    updated = admin.companion.update_persona("live-user", PersonaConfig(tone="gentle", use_emoji=True))
    assert updated.persona.use_emoji is True

    fact = admin.companion.add_fact("live-user", "Builds the Atlas agent platform", category="project")
    assert fact.id > 0 and fact.source.startswith("api:")
    hits = admin.companion.list_facts("live-user", query="Atlas")
    assert hits and hits[0].id == fact.id
    retired = admin.companion.retire_fact("live-user", fact.id)
    assert retired.active is False
    assert admin.companion.list_facts("live-user") == []


def test_live_chat_roundtrip_with_fact_extraction(admin: MeemeeClient) -> None:
    admin.companion.upsert_user("chat-user", "Chat User")
    reply = admin.companion.chat("chat-user", "hello companion")
    assert reply.reply == REPLY_TEXT
    assert reply.facts_learned == 1
    conversations = admin.companion.list_conversations("chat-user")
    assert conversations[0].id == reply.conversation_id
    messages = admin.companion.messages(reply.conversation_id)
    assert [m.role for m in messages] == ["user", "assistant"]
    facts = admin.companion.list_facts("chat-user")
    assert facts[0].text == FACT_TEXT
    assert facts[0].source.startswith("conversation:")

    second = admin.companion.chat("chat-user", "again", conversation_id=reply.conversation_id)
    assert second.conversation_id == reply.conversation_id
    assert len(admin.companion.messages(reply.conversation_id)) == 4


def test_live_checkin_plan_tick_and_disable(admin: MeemeeClient) -> None:
    from meemee_client import CheckInPreferences

    admin.companion.upsert_user(
        "plan-user", "Plan User",
        checkins=CheckInPreferences(enabled=True, cadence_minutes=15),
    )
    planned = admin.companion.plan_checkin("plan-user")
    assert planned.status == "queued"
    again = admin.companion.plan_checkin("plan-user")
    assert again.id == planned.id

    tick = admin.companion.tick()
    assert tick.planned >= 1

    past = admin.companion.plan_checkin("plan-user")
    assert past.status == "queued"
    deliveries = admin.companion.tick().deliveries
    assert isinstance(deliveries, list)

    disabled = admin.companion.update_checkins("plan-user", CheckInPreferences(enabled=False))
    assert disabled.cancelled_pending >= 1
    queued = admin.companion.list_checkins("plan-user", status="queued")
    assert queued == []


def test_live_companion_scope_enforced(live_server: str, admin: MeemeeClient) -> None:
    minted = admin.tokens.create("jobs-only-live", {"jobs:read"})
    with MeemeeClient(live_server, auth=minted.token) as scoped, pytest.raises(PermissionDeniedError):
        scoped.companion.list_users()
    reader = admin.tokens.create("companion-reader-live", {"companion:read"})
    with MeemeeClient(live_server, auth=reader.token) as scoped:
        assert scoped.companion.list_users() is not None
        with pytest.raises(PermissionDeniedError):
            scoped.companion.upsert_user("denied", "Denied")
