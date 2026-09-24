"""Live: AsyncOIDCClientCredentialsAuth against a real local HTTPS OIDC issuer and a booted Meemee server.

The issuer is a small HTTPS server in this process with a self-signed
certificate: OpenID discovery, a client_credentials token endpoint (HTTP
Basic client auth) minting RS256 JWTs, and a JWKS document. The Meemee
server is configured with MEEMEE_OIDC_ISSUER/AUDIENCE/JWKS_URL and trusts the
certificate through SSL_CERT_FILE, so it validates every token signature,
issuer, audience and expiry for real.
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import os
import ssl
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from meemee_client import (
    AsyncMeemeeClient,
    AsyncOIDCClientCredentialsAuth,
    AuthenticationError,
    MeemeeClient,
    PermissionDeniedError,
)
from test_live_integration import REPO_ROOT, SERVER_PACKAGE, _free_port

jwt = pytest.importorskip("jwt")
crypto = pytest.importorskip("cryptography")
pytestmark = pytest.mark.skipif(not (SERVER_PACKAGE / "api.py").exists(), reason="meemee server package not found")

CLIENT_ID, CLIENT_SECRET, AUDIENCE = "svc-worker", "s3cret-value", "meemee"


def _self_signed(tmp: Path):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp / "cert.pem", tmp / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return cert_path, key_path


class Issuer:
    def __init__(self, tmp: Path) -> None:
        from cryptography.hazmat.primitives.asymmetric import rsa
        self.cert, key = _self_signed(tmp)
        self.signing = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.token_requests = 0
        self.port = _free_port()
        self.url = f"https://127.0.0.1:{self.port}"
        issuer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def _json(self, status: int, body: dict) -> None:
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                if self.path == "/.well-known/openid-configuration":
                    self._json(200, {"issuer": issuer.url, "token_endpoint": f"{issuer.url}/token",
                                     "jwks_uri": f"{issuer.url}/jwks.json"})
                elif self.path == "/jwks.json":
                    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(issuer.signing.public_key()))
                    self._json(200, {"keys": [{**jwk, "kid": "live-key", "use": "sig", "alg": "RS256"}]})
                else:
                    self._json(404, {})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
                expected = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
                if self.path != "/token" or form.get("grant_type") != "client_credentials":
                    self._json(400, {"error": "unsupported_grant_type"})
                    return
                if self.headers.get("Authorization") != expected:
                    self._json(401, {"error": "invalid_client", "error_description": "client authentication failed"})
                    return
                issuer.token_requests += 1
                now = datetime.now(timezone.utc)
                claims = {"iss": issuer.url, "aud": AUDIENCE, "sub": CLIENT_ID, "iat": now,
                          "exp": now + timedelta(minutes=10), "roles": form.get("scope", "operator").split(),
                          "jti": uuid.uuid4().hex}
                token = jwt.encode(claims, issuer.signing, algorithm="RS256", headers={"kid": "live-key"})
                self._json(200, {"access_token": token, "token_type": "Bearer", "expires_in": 600})

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self.cert, key)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(scope="module")
def oidc_stack(tmp_path_factory: pytest.TempPathFactory):
    tmp = tmp_path_factory.mktemp("oidc")
    issuer = Issuer(tmp)
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "MEEMEE_API_TOKEN": "bootstrap-oidc-token",
           "MEEMEE_DATA_DIR": str(tmp / "data"), "MEEMEE_VAULT_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
           "MEEMEE_OIDC_ISSUER": issuer.url, "MEEMEE_OIDC_AUDIENCE": AUDIENCE,
           "MEEMEE_OIDC_JWKS_URL": f"{issuer.url}/jwks.json", "SSL_CERT_FILE": str(issuer.cert)}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "meemee.api:app", "--host", "127.0.0.1", "--port", str(port)],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            issuer.stop()
            pytest.fail(f"server exited during startup:\n{proc.stdout.read() if proc.stdout else ''}")
        try:
            if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                break
        except httpx.TransportError:
            time.sleep(0.25)
    else:
        proc.kill()
        issuer.stop()
        pytest.fail("server did not become healthy within 20s")
    yield base, issuer
    proc.kill()
    proc.wait(timeout=10)
    issuer.stop()


class Clock:
    def __init__(self) -> None:
        self.offset = 0.0

    def __call__(self) -> float:
        return time.time() + self.offset


def _provider(issuer: Issuer, *, secret: str = CLIENT_SECRET, scope: str | None = "operator", clock=None):
    tls = ssl.create_default_context(cafile=str(issuer.cert))
    return AsyncOIDCClientCredentialsAuth(issuer.url, client_id=CLIENT_ID, client_secret=secret, scope=scope,
                                          http_client=httpx.AsyncClient(verify=tls, timeout=10),
                                          clock=clock or time.time)


async def test_live_async_oidc_token_is_validated_by_the_server(oidc_stack) -> None:
    base, issuer = oidc_stack
    before = issuer.token_requests
    auth = _provider(issuer)
    async with AsyncMeemeeClient(base, auth=auth) as client:
        created = await client.jobs.create("OIDC live goal", run_at="2099-01-01T00:00:00Z")
        listed = await asyncio.gather(*(client.jobs.list() for _ in range(8)))
        assert all(created.id in [j.id for j in page] for page in listed)
        assert (await client.jobs.cancel(created.id)).status.value == "cancelled"
        with pytest.raises(PermissionDeniedError) as caught:
            await client.tokens.create("nope", {"admin"})
        assert caught.value.missing_scope == "admin"
    assert issuer.token_requests - before == 1  # one fetch served every concurrent request
    await auth.aclose()


async def test_live_async_oidc_refresh_yields_a_new_accepted_token(oidc_stack) -> None:
    base, issuer = oidc_stack
    clock = Clock()
    auth = _provider(issuer, clock=clock)
    async with AsyncMeemeeClient(base, auth=auth) as client:
        await client.jobs.list()
        first = await auth.access_token()
        clock.offset = 600  # jump past expiry minus leeway on the client's clock only
        await client.jobs.list()
        second = await auth.access_token()
        assert first != second
    with MeemeeClient(base, auth="bootstrap-oidc-token") as admin:
        seen = admin.tokens.introspect(second)
        assert seen.active and seen.credential == "oidc" and seen.principal == f"oidc:{CLIENT_ID}"
        assert "jobs:write" in seen.scopes
    await auth.aclose()


async def test_live_async_oidc_rejections(oidc_stack) -> None:
    base, issuer = oidc_stack
    bad = _provider(issuer, secret="wrong-secret")
    async with AsyncMeemeeClient(base, auth=bad) as client:
        with pytest.raises(AuthenticationError, match="invalid_client"):
            await client.jobs.list()
    await bad.aclose()
    # A validly signed token whose roles map to no scopes authenticates but is denied.
    no_role = _provider(issuer, scope="unmapped-role")
    async with AsyncMeemeeClient(base, auth=no_role) as client:
        with pytest.raises(PermissionDeniedError):
            await client.jobs.list()
    await no_role.aclose()


async def test_live_server_rejects_a_forged_token(oidc_stack) -> None:
    from cryptography.hazmat.primitives.asymmetric import rsa
    base, issuer = oidc_stack
    now = datetime.now(timezone.utc)
    forged = jwt.encode({"iss": issuer.url, "aud": AUDIENCE, "sub": CLIENT_ID, "iat": now,
                         "exp": now + timedelta(minutes=5), "roles": ["admin"]},
                        rsa.generate_private_key(public_exponent=65537, key_size=2048),
                        algorithm="RS256", headers={"kid": "live-key"})
    async with AsyncMeemeeClient(base, auth=forged) as client:
        with pytest.raises(AuthenticationError):
            await client.jobs.list()
