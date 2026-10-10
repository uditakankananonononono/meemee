"""Real store and controlled-transport verification. No live destination effects."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from meemee.companion.models import CheckInPreferences, UserProfile
from meemee.companion.store import CompanionStore


def profile(store):
    store.upsert_user(
        UserProfile(
            user_id="owner",
            display_name="Owner",
            checkins=CheckInPreferences(
                enabled=True, channel="webhook", address="https://example.com/receiver"
            ),
        )
    )


@pytest.mark.asyncio
async def test_owner_webhook_proof_issuance_replay_revocation_and_send_intent(
    tmp_path, monkeypatch
):
    from meemee.companion.destination_control import verify_webhook_control

    store = CompanionStore(tmp_path / "c.db")
    profile(store)
    observed = []

    def endpoint(request):
        import json

        body = json.loads(request.content)
        observed.append(body)
        assert (
            set(body) == {"kind", "challenge_id", "nonce"}
            and body["kind"] == "meemee.destination_control"
        )
        return httpx.Response(
            200, json={"challenge_id": body["challenge_id"], "nonce": body["nonce"]}
        )

    # Explicit SSRF harness, not a production DNS bypass.
    monkeypatch.setattr(
        "meemee.companion.destination_control.validate_webhook_url", lambda url: url
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
        grant = await verify_webhook_control(
            store, "owner", "https://example.com/receiver", client=client
        )
    assert grant["channel"] == "webhook" and grant["destination"] == "https://example.com/receiver"
    assert "nonce" not in grant and "nonce_sha256" not in grant
    assert store.destination_is_verified("owner", "webhook", "https://example.com/receiver")
    assert not store.destination_is_verified("other", "webhook", "https://example.com/receiver")
    now = datetime.now(timezone.utc)
    store.schedule_checkin("owner", now, "one", "webhook", "https://example.com/receiver")
    claim = store.claim_checkin_fenced()
    assert store.revoke_destination("owner", grant["id"])
    assert not store.start_checkin_delivery(claim)
    assert store.list_checkins("owner")[0]["status"] == "cancelled"
    assert not store.destination_is_verified("owner", "whatsapp", "+12345678901")
    store.db.close()


def test_missing_expired_revoked_and_nonce_owner_checks(tmp_path):
    from meemee.companion.destination_control import DestinationVerificationError

    s = CompanionStore(tmp_path / "c.db")
    profile(s)
    now = datetime.now(timezone.utc)
    challenge, nonce = s.create_destination_challenge("owner", "https://example.com/receiver", now)
    assert "nonce" not in challenge and "nonce_sha256" not in challenge
    with pytest.raises(DestinationVerificationError):
        s.consume_destination_challenge("other", challenge["id"], nonce, now)
    with pytest.raises(DestinationVerificationError):
        s.consume_destination_challenge("owner", challenge["id"], "wrong", now)
    grant = s.consume_destination_challenge("owner", challenge["id"], nonce, now)
    with pytest.raises(DestinationVerificationError):
        s.consume_destination_challenge("owner", challenge["id"], nonce, now)
    assert not s.destination_is_verified(
        "owner", "webhook", grant["destination"], now + timedelta(days=7)
    )
    challenge, nonce = s.create_destination_challenge(
        "owner", "https://example.com/receiver", now + timedelta(seconds=2)
    )
    with pytest.raises(DestinationVerificationError):
        s.consume_destination_challenge(
            "owner", challenge["id"], nonce, now + timedelta(seconds=62)
        )
    s.db.close()


@pytest.fixture(params=["sqlite", "pg"])
def make(request, tmp_path):
    if request.param == "sqlite":
        handles = []

        def factory():
            s = CompanionStore(tmp_path / "parity.db")
            handles.append(s.db)
            return s

        yield factory
        for db in handles:
            db.close()
    else:
        import pgserver

        from meemee_persist_pg import Database, MigrationStore
        from meemee_persist_pg.companion import CompanionStore as PG

        server = pgserver.get_server(tmp_path / "pg", cleanup_mode="stop")
        db = Database(server.get_uri(), max_size=12)
        assert 21 in MigrationStore(db).apply()
        with db.transaction() as c:
            print("ACTUAL_PG", c.execute("SELECT version() AS v").fetchone()["v"], "MIGRATION021")
        try:
            yield lambda: PG(db)
        finally:
            db.close()
            server.cleanup()


def test_parity_exact_owner_destination_replay_reopen_delete(make):
    from meemee.companion.destination_control import DestinationVerificationError

    s = make()
    profile(s)
    now = datetime.now(timezone.utc)
    challenge, nonce = s.create_destination_challenge("owner", "https://example.com/receiver", now)
    grant = make().consume_destination_challenge("owner", challenge["id"], nonce, now)
    assert make().destination_is_verified("owner", "webhook", grant["destination"], now)
    assert not make().destination_is_verified("owner", "webhook", grant["destination"] + "/", now)
    assert not make().destination_is_verified("other", "webhook", grant["destination"], now)
    with pytest.raises(DestinationVerificationError):
        make().consume_destination_challenge("owner", challenge["id"], nonce, now)
    assert not make().revoke_destination("other", grant["id"])
    assert make().revoke_destination("owner", grant["id"])
    assert not s.destination_is_verified("owner", "webhook", grant["destination"], now)
    counts = s.delete_user_data("owner")
    assert counts["destination_grants"] == 1 and counts["destination_challenges"] == 1
    profile(s)
    assert not make().destination_is_verified("owner", "webhook", grant["destination"], now)


def test_concurrent_consumption_exactly_one_and_issuer_throttle(make):
    from concurrent.futures import ThreadPoolExecutor

    from meemee.companion.destination_control import DestinationVerificationError

    s = make()
    profile(s)
    now = datetime.now(timezone.utc)
    challenge, nonce = s.create_destination_challenge("owner", "https://example.com/receiver", now)
    handles = [make() for _ in range(6)]

    def consume(handle):
        try:
            return handle.consume_destination_challenge("owner", challenge["id"], nonce, now)
        except DestinationVerificationError:
            return None

    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(consume, handles))
    assert len([r for r in results if r]) == 1
    with pytest.raises(DestinationVerificationError):
        s.create_destination_challenge("owner", "https://example.com/receiver", now)
    for i in range(1, 10):
        s.create_destination_challenge(
            "owner", "https://example.com/receiver", now + timedelta(seconds=2 * i)
        )
    with pytest.raises(DestinationVerificationError):
        s.create_destination_challenge(
            "owner", "https://example.com/receiver", now + timedelta(seconds=20)
        )
    s.create_destination_challenge(
        "owner", "https://example.com/receiver", now + timedelta(seconds=80)
    )


@pytest.mark.parametrize(
    "channel,address",
    [
        ("webhook", "https://example.com/receiver"),
        ("whatsapp", "+12345678901"),
        ("imessage", "+12345678901"),
    ],
)
async def test_real_worker_refuses_unverifiable_before_adapter(make, channel, address):
    from meemee.companion.channels import DeliveryResult
    from meemee.companion.worker import deliver_due_once

    s = make()
    s.upsert_user(
        UserProfile(
            user_id="owner",
            display_name="Owner",
            checkins=CheckInPreferences(enabled=True, channel=channel, address=address),
        )
    )
    s.schedule_checkin(
        "owner", datetime.now(timezone.utc) - timedelta(seconds=1), "one", channel, address
    )

    class Engine:
        async def checkin_message(self, user):
            return "private message must not leave"

    class Adapter:
        calls = 0

        async def send(self, *args):
            self.calls += 1
            return DeliveryResult(channel, address, True, "should not happen")

    adapter = Adapter()
    result = await deliver_due_once(s, Engine(), {channel: adapter})
    assert not result["delivered"] and result["status"] == "cancelled" and adapter.calls == 0
    assert s.list_checkins("owner")[0]["delivery_state"] == "not_started"


@pytest.mark.parametrize("mode", ["valid", "expired", "revoked", "transfer"])
def test_intent_gate_verified_expired_revoked_or_transferred(make, mode):
    s = make()
    profile(s)
    now = datetime.now(timezone.utc)
    challenge, nonce = s.create_destination_challenge("owner", "https://example.com/receiver", now)
    grant = s.consume_destination_challenge("owner", challenge["id"], nonce, now)
    when = now + timedelta(days=7) if mode == "expired" else now
    s.schedule_checkin("owner", when, "one", "webhook", "https://example.com/receiver")
    claim = s.claim_checkin_fenced(when)
    if mode == "revoked":
        make().revoke_destination("owner", grant["id"])
    if mode == "transfer":
        p = s.profile("owner")
        p.checkins.address = "https://example.com/new"
        s.upsert_user(p)
    assert s.start_checkin_delivery(claim, when) == (mode == "valid")


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(302, headers={"Location": "https://elsewhere.com"}),
        httpx.Response(500),
        httpx.Response(200, json={"nonce": "bad", "challenge_id": "bad"}),
        httpx.Response(200, content=b"x" * 2049),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"extra": "field"}),
        httpx.Response(200, json={"nonce": "\u00e9", "challenge_id": "bad"}),
    ],
)
async def test_refuse_endpoint_failures_without_grant(tmp_path, monkeypatch, response):
    from meemee.companion.destination_control import (
        DestinationVerificationError,
        verify_webhook_control,
    )

    s = CompanionStore(tmp_path / "fail.db")
    profile(s)
    monkeypatch.setattr(
        "meemee.companion.destination_control.validate_webhook_url", lambda url: url
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
        with pytest.raises(DestinationVerificationError):
            await verify_webhook_control(s, "owner", "https://example.com/receiver", client=client)
    assert s.list_destination_grants("owner") == []
    s.db.close()


@pytest.mark.parametrize(
    "destination",
    [
        "http://example.com",
        "https://user:pass@example.com",
        " https://example.com",
        "https://example.com/#secret",
        "https://example.com:0",
        "https://example.com/\n",
        "https://example.com\\evil",
    ],
)
def test_syntax_fail_closed_before_challenge(tmp_path, destination):
    from meemee.companion.destination_control import DestinationVerificationError

    s = CompanionStore(tmp_path / "bad.db")
    profile(s)
    with pytest.raises(DestinationVerificationError):
        s.create_destination_challenge("owner", destination)
    assert s.list_destination_grants("owner") == []
    s.db.close()


def test_owner_api_admin_no_bypass_and_no_self_assertion(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI, Header, HTTPException
    from fastapi.testclient import TestClient

    from meemee.companion.api import build_companion_router

    s = CompanionStore(tmp_path / "api.db")
    profile(s)

    class Auth:
        def dependency(self, scope):
            def check(x_principal: str = Header(default=""), x_scopes: str = Header(default="")):
                scopes = frozenset(x_scopes.split(","))
                if not x_principal:
                    raise HTTPException(401)
                if scope not in scopes and "admin" not in scopes:
                    raise HTTPException(403)
                return SimpleNamespace(id=x_principal, scopes=scopes)

            return check

    async def proof(store, owner, destination):
        challenge, nonce = store.create_destination_challenge(owner, destination)
        return store.consume_destination_challenge(owner, challenge["id"], nonce)

    monkeypatch.setattr("meemee.companion.destination_control.verify_webhook_control", proof)
    app = FastAPI()
    app.include_router(build_companion_router(s, None, {}, Auth()))
    with TestClient(app) as client:
        route = "/v1/companion/users/owner/destinations"
        assert client.get(route).status_code == 401
        assert (
            client.get(route, headers={"x-principal": "owner", "x-scopes": "jobs:read"}).status_code
            == 403
        )
        for identity in ["other", "admin"]:
            headers = {"x-principal": identity, "x-scopes": "admin"}
            assert (
                client.post(
                    route + "/webhook/verify",
                    headers=headers,
                    json={"destination": "https://example.com/receiver"},
                ).status_code
                == 404
            )
            assert client.get(route, headers=headers).status_code == 404
        headers = {"x-principal": "owner", "x-scopes": "companion:read,companion:write"}
        assert (
            client.post(
                route + "/webhook/verify",
                headers=headers,
                json={
                    "destination": "https://example.com/receiver",
                    "verifier_method": "otp_confirmed",
                },
            ).status_code
            == 422
        )
        result = client.post(
            route + "/webhook/verify",
            headers=headers,
            json={"destination": "https://example.com/receiver"},
        )
        assert result.status_code == 201
        assert "nonce" not in result.text
        grant = result.json()
        assert client.get(route, headers=headers).json()["grants"] == [grant]
        assert (
            client.delete(
                route + "/" + grant["id"], headers={"x-principal": "admin", "x-scopes": "admin"}
            ).status_code
            == 404
        )
        assert client.delete(route + "/" + grant["id"], headers=headers).status_code == 200
        assert not s.destination_is_verified("owner", "webhook", grant["destination"])
    s.db.close()


async def test_actual_loopback_tls_receiver_proof_and_worker_send(tmp_path, monkeypatch):
    # Explicit SSRF TLS test harness. No external destinations or public cert.
    import json
    import ssl
    import subprocess
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from meemee.companion.channels import WebhookChannel
    from meemee.companion.destination_control import verify_webhook_control
    from meemee.companion.worker import deliver_due_once

    cert = tmp_path / "cert.pem"
    key = tmp_path / "key.pem"
    import asyncio

    await asyncio.to_thread(
        subprocess.run,
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
        ],
        check=True,
        capture_output=True,
    )
    received = []

    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(data)
            response = (
                json.dumps({"challenge_id": data["challenge_id"], "nonce": data["nonce"]})
                if data["kind"] == "meemee.destination_control"
                else "{}"
            )
            raw = response.encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert), str(key))
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("MEEMEE_WEBHOOK_ALLOW_PRIVATE_HOSTS", "1")
    monkeypatch.setenv("MEEMEE_ENV", "test")
    s = CompanionStore(tmp_path / "tls.db")
    destination = f"https://localhost:{server.server_port}/receiver"
    s.upsert_user(
        UserProfile(
            user_id="owner",
            display_name="Owner",
            checkins=CheckInPreferences(enabled=True, channel="webhook", address=destination),
        )
    )

    class Engine:
        async def checkin_message(self, user):
            return "controlled harness message"

    trust = ssl.create_default_context(cafile=str(cert))
    try:
        async with httpx.AsyncClient(verify=trust, trust_env=False) as client:
            grant = await verify_webhook_control(s, "owner", destination, client=client)
            assert grant["destination"] == destination
            s.schedule_checkin(
                "owner",
                datetime.now(timezone.utc) - timedelta(seconds=1),
                "one",
                "webhook",
                destination,
            )
            result = await deliver_due_once(s, Engine(), {"webhook": WebhookChannel(client=client)})
            assert result["delivered"]
        assert [item["kind"] for item in received] == [
            "meemee.destination_control",
            "companion.message",
        ]
        assert set(received[0]) == {"kind", "challenge_id", "nonce"}
        assert received[1]["text"] == "controlled harness message"
        assert received[0]["nonce"] not in json.dumps(s.export_user_data("owner"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        s.db.close()


def test_production_private_override_refused(tmp_path, monkeypatch):
    from meemee.webhooks import validate_webhook_url

    monkeypatch.setenv("MEEMEE_ENV", "production")
    monkeypatch.setenv("MEEMEE_WEBHOOK_ALLOW_PRIVATE_HOSTS", "1")
    with pytest.raises(RuntimeError):
        validate_webhook_url("https://127.0.0.1/")


def test_real_offline_cutover_preserves_proof_and_legacy_empty(tmp_path):
    import pgserver

    from meemee_persist_pg import Database, MigrationStore
    from meemee_persist_pg.companion import CompanionStore as PG
    from meemee_persist_pg.cutover import SPECS, Cutover

    source = tmp_path / "source.db"
    s = CompanionStore(source)
    profile(s)
    challenge, nonce = s.create_destination_challenge("owner", "https://example.com/receiver")
    grant = s.consume_destination_challenge("owner", challenge["id"], nonce)
    s.db.close()
    server = pgserver.get_server(tmp_path / "pgcopy", cleanup_mode="stop")
    db = Database(server.get_uri())
    MigrationStore(db).apply()
    try:
        # Exact real production copier, narrowed to companion for bounded seam test.
        cut = Cutover.__new__(Cutover)
        cut.db = db
        cut.sources = {"companion": source}
        cut.specs = {"companion": SPECS["companion"]}
        result = cut.copy()
        assert result["meemee_companion_destination_grants"] == 1
        assert all(r["match"] for r in cut.verify().values())
        target = PG(db)
        assert target.destination_is_verified("owner", "webhook", grant["destination"])
        import sqlite3

        from meemee_persist_pg.cutover import source_query, sqlite_snapshot

        with sqlite3.connect(source) as legacy:
            legacy.execute("DROP TABLE companion_destination_challenges")
            legacy.execute("DROP TABLE companion_destination_grants")
        with sqlite_snapshot(source) as legacy:
            for spec in SPECS["companion"][-2:]:
                assert legacy.execute(source_query(spec, legacy)).fetchall() == []
    finally:
        db.close()
        server.cleanup()


@pytest.mark.parametrize("action", ["consume", "intent"])
def test_post_lock_fresh_clock_refuses_expired_proof(make, action):
    import time
    from concurrent.futures import ThreadPoolExecutor

    from meemee.companion.destination_control import DestinationVerificationError

    s = make()
    profile(s)
    now = datetime.now(timezone.utc)
    challenge, nonce = s.create_destination_challenge("owner", "https://example.com/receiver", now)
    if action == "consume":
        table = "companion_destination_challenges"
        column = "expires_at"
        ident = challenge["id"]
    else:
        grant = s.consume_destination_challenge("owner", challenge["id"], nonce, now)
        table = "companion_destination_grants"
        column = "expires_at"
        ident = grant["id"]
        s.schedule_checkin("owner", now, "one", "webhook", "https://example.com/receiver")
        claim = s.claim_checkin_fenced()
    # Shorten only fixture rows; production lifetimes are fixed 60s/7d.
    with s._lease_transaction() as c:
        c.execute(
            f"UPDATE {table} SET {column}=? WHERE id=?",
            ((datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(), ident),
        )
    other = make()

    def attempt():
        if action == "consume":
            try:
                other.consume_destination_challenge("owner", challenge["id"], nonce)
                return True
            except DestinationVerificationError:
                return False
        return other.start_checkin_delivery(claim)

    pool = ThreadPoolExecutor(1)
    try:
        with s._lease_transaction() as c:
            c.execute(f"SELECT id FROM {table} WHERE id=?" + c.for_update, (ident,)).fetchone()
            future = pool.submit(attempt)
            time.sleep(1.4)
            assert not future.done()
        assert future.result(timeout=5) is False
    finally:
        pool.shutdown()


async def test_exact_challenge_id_wrong_nonce_refuses(tmp_path, monkeypatch):
    import json

    from meemee.companion.destination_control import (
        DestinationVerificationError,
        verify_webhook_control,
    )

    s = CompanionStore(tmp_path / "nonce.db")
    profile(s)
    monkeypatch.setattr(
        "meemee.companion.destination_control.validate_webhook_url", lambda url: url
    )

    def receiver(request):
        body = json.loads(request.content)
        return httpx.Response(200, json={"challenge_id": body["challenge_id"], "nonce": "wrong"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(receiver)) as client:
        with pytest.raises(DestinationVerificationError):
            await verify_webhook_control(s, "owner", "https://example.com/receiver", client=client)
    assert s.list_destination_grants("owner") == []
    s.db.close()


@pytest.mark.parametrize("change", ["revoke", "delete"])
def test_revocation_or_delete_commits_before_waiting_intent(make, change):
    import time
    from concurrent.futures import ThreadPoolExecutor

    from meemee.companion.checkin_leases import LostCheckinClaim

    s = make()
    profile(s)
    now = datetime.now(timezone.utc)
    challenge, nonce = s.create_destination_challenge("owner", "https://example.com/receiver")
    grant = s.consume_destination_challenge("owner", challenge["id"], nonce)
    s.schedule_checkin("owner", now, "one", "webhook", "https://example.com/receiver")
    claim = s.claim_checkin_fenced()
    other = make()

    def intent():
        try:
            return other.start_checkin_delivery(claim)
        except LostCheckinClaim:
            return False

    pool = ThreadPoolExecutor(1)
    try:
        with s._lease_transaction() as c:
            if change == "revoke":
                c.execute(
                    "SELECT id FROM companion_destination_grants WHERE id=?" + c.for_update,
                    (grant["id"],),
                ).fetchone()
                future = pool.submit(intent)
                time.sleep(0.2)
                assert not future.done()
                c.execute(
                    "UPDATE companion_destination_grants SET revoked_at=? WHERE id=?",
                    (datetime.now(timezone.utc).isoformat(), grant["id"]),
                )
            else:
                # Match actual delete order: checkin -> profile -> grants.
                c.execute(
                    "SELECT id FROM companion_checkins WHERE id=?" + c.for_update, (claim["id"],)
                ).fetchone()
                future = pool.submit(intent)
                time.sleep(0.2)
                assert not future.done()
                c.execute("DELETE FROM companion_checkins WHERE user_id=?", ("owner",))
                c.execute("DELETE FROM companion_destination_grants WHERE owner_id=?", ("owner",))
                c.execute("DELETE FROM companion_users WHERE user_id=?", ("owner",))
        assert future.result(timeout=5) is False
    finally:
        pool.shutdown()


def test_sqlite_legacy_upgrade_no_auto_attestation(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.db"
    s = CompanionStore(path)
    profile(s)
    s.db.close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE companion_destination_challenges")
        db.execute("DROP TABLE companion_destination_grants")
    s = CompanionStore(path)
    assert s.list_destination_grants("owner") == []
    assert not s.destination_is_verified("owner", "webhook", "https://example.com/receiver")
    s.db.close()


async def test_private_dns_and_production_override_no_http(tmp_path, monkeypatch):
    import socket

    from meemee.companion.destination_control import (
        DestinationVerificationError,
        verify_webhook_control,
    )

    s = CompanionStore(tmp_path / "dns.db")
    profile(s)
    calls = []
    monkeypatch.setenv("MEEMEE_WEBHOOK_ALLOW_PRIVATE_HOSTS", "0")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: calls.append(r) or httpx.Response(200))
    ) as client:
        with pytest.raises(DestinationVerificationError):
            await verify_webhook_control(s, "owner", "https://example.com/receiver", client=client)
        monkeypatch.setenv("MEEMEE_ENV", "production")
        monkeypatch.setenv("MEEMEE_WEBHOOK_ALLOW_PRIVATE_HOSTS", "1")
        with pytest.raises(DestinationVerificationError):
            await verify_webhook_control(s, "owner", "https://example.com/receiver", client=client)
    assert calls == [] and s.list_destination_grants("owner") == []
    s.db.close()


async def test_cancelled_exchange_leaves_no_grant(tmp_path, monkeypatch):
    import asyncio

    from meemee.companion.destination_control import verify_webhook_control

    s = CompanionStore(tmp_path / "cancel.db")
    profile(s)
    entered = asyncio.Event()
    monkeypatch.setattr(
        "meemee.companion.destination_control.validate_webhook_url", lambda url: url
    )

    async def receiver(request):
        entered.set()
        await asyncio.sleep(100)

    async with httpx.AsyncClient(transport=httpx.MockTransport(receiver)) as client:
        task = asyncio.create_task(
            verify_webhook_control(s, "owner", "https://example.com/receiver", client=client)
        )
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert s.list_destination_grants("owner") == []
    s.db.close()


async def test_compressed_response_refused(tmp_path, monkeypatch):
    import gzip
    import json

    from meemee.companion.destination_control import (
        DestinationVerificationError,
        verify_webhook_control,
    )

    s = CompanionStore(tmp_path / "gzip.db")
    profile(s)
    monkeypatch.setattr(
        "meemee.companion.destination_control.validate_webhook_url", lambda url: url
    )

    def receiver(request):
        body = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            content=gzip.compress(
                json.dumps({"challenge_id": body["challenge_id"], "nonce": body["nonce"]}).encode()
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(receiver)) as client:
        with pytest.raises(DestinationVerificationError):
            await verify_webhook_control(s, "owner", "https://example.com/receiver", client=client)
    assert s.list_destination_grants("owner") == []
    s.db.close()
