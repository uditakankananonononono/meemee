"""Auth providers: static tokens and OIDC client-credentials with discovery."""
from __future__ import annotations

import httpx
import pytest

from meemee_client import AuthenticationError, MeemeeError, OIDCClientCredentialsAuth, TokenAuth


def test_token_auth_header() -> None:
    assert TokenAuth("mee_abc").authorization_header() == "Bearer mee_abc"


def test_token_auth_rejects_empty() -> None:
    with pytest.raises(ValueError):
        TokenAuth("   ")


def _oidc_transport(*, discovery: bool = True, expires_in: int = 3600, calls: dict | None = None) -> httpx.MockTransport:
    calls = calls if calls is not None else {"discovery": 0, "token": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            calls["discovery"] = calls.get("discovery", 0) + 1
            return httpx.Response(200, json={"token_endpoint": "http://idp.test/oauth/token"})
        if request.url.path == "/oauth/token":
            calls["token"] = calls.get("token", 0) + 1
            return httpx.Response(200, json={
                "access_token": f"oidc-token-{calls['token']}",
                "token_type": "Bearer",
                "expires_in": expires_in,
            })
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_oidc_discovers_endpoint_and_fetches_token() -> None:
    calls: dict = {}
    transport = _oidc_transport(calls=calls)
    auth = OIDCClientCredentialsAuth(
        "http://idp.test", client_id="meemee-worker", client_secret="s3cret",
        http_client=httpx.Client(transport=transport),
    )
    assert auth.authorization_header() == "Bearer oidc-token-1"
    assert calls == {"discovery": 1, "token": 1}


def test_oidc_caches_token_until_near_expiry() -> None:
    calls: dict = {}
    auth = OIDCClientCredentialsAuth(
        "http://idp.test", client_id="c", client_secret="s",
        http_client=httpx.Client(transport=_oidc_transport(calls=calls)),
    )
    auth.authorization_header()
    auth.authorization_header()
    assert calls["token"] == 1


def test_oidc_refetches_when_inside_leeway() -> None:
    calls: dict = {}
    auth = OIDCClientCredentialsAuth(
        "http://idp.test", client_id="c", client_secret="s", leeway_seconds=60,
        http_client=httpx.Client(transport=_oidc_transport(calls=calls, expires_in=30)),
    )
    first = auth.authorization_header()
    second = auth.authorization_header()
    assert first != second
    assert calls["token"] == 2


def test_oidc_refresh_forces_new_token() -> None:
    calls: dict = {}
    auth = OIDCClientCredentialsAuth(
        "http://idp.test", client_id="c", client_secret="s",
        http_client=httpx.Client(transport=_oidc_transport(calls=calls)),
    )
    first = auth.access_token
    auth.refresh()
    assert auth.access_token != first
    assert calls["token"] == 2


def test_oidc_explicit_token_endpoint_skips_discovery() -> None:
    calls: dict = {}
    auth = OIDCClientCredentialsAuth(
        token_endpoint="http://idp.test/oauth/token", client_id="c", client_secret="s",
        http_client=httpx.Client(transport=_oidc_transport(calls=calls)),
    )
    auth.authorization_header()
    assert calls.get("discovery", 0) == 0
    assert calls["token"] == 1


def test_oidc_scope_is_sent_in_grant() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode()
        seen["auth"] = request.headers.get("Authorization", "")
        return httpx.Response(200, json={"access_token": "t", "token_type": "Bearer", "expires_in": 60})

    auth = OIDCClientCredentialsAuth(
        token_endpoint="http://idp.test/oauth/token", client_id="meemee", client_secret="pw",
        scope="operator", http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    auth.authorization_header()
    assert "grant_type=client_credentials" in seen["body"]
    assert "scope=operator" in seen["body"]
    assert seen["auth"].startswith("Basic ")


def test_oidc_token_error_surfaces_error_description() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_client", "error_description": "bad secret"})

    auth = OIDCClientCredentialsAuth(
        token_endpoint="http://idp.test/oauth/token", client_id="c", client_secret="wrong",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(AuthenticationError, match="bad secret"):
        auth.authorization_header()


def test_oidc_missing_access_token_is_an_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"token_type": "Bearer"})

    auth = OIDCClientCredentialsAuth(
        token_endpoint="http://idp.test/oauth/token", client_id="c", client_secret="s",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(AuthenticationError, match="access_token"):
        auth.authorization_header()


def test_oidc_non_bearer_token_type_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "t", "token_type": "mac", "expires_in": 60})

    auth = OIDCClientCredentialsAuth(
        token_endpoint="http://idp.test/oauth/token", client_id="c", client_secret="s",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(AuthenticationError, match="token_type"):
        auth.authorization_header()


def test_oidc_requires_issuer_or_endpoint() -> None:
    with pytest.raises(ValueError):
        OIDCClientCredentialsAuth(client_id="c", client_secret="s")


def test_oidc_requires_client_credentials() -> None:
    with pytest.raises(ValueError):
        OIDCClientCredentialsAuth(token_endpoint="http://idp.test/token", client_id="", client_secret="s")
