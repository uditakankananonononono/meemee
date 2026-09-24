"""AsyncOIDCClientCredentialsAuth against a mocked OIDC issuer."""
from __future__ import annotations

import asyncio
import base64

import httpx
import pytest
from meemee_client import (
    AsyncMeemeeClient,
    AsyncOIDCClientCredentialsAuth,
    AuthenticationError,
    MeemeeClient,
    MeemeeError,
    OIDCClientCredentialsAuth,
)

ISSUER = "http://idp.test"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def issuer(calls: dict, *, expires_in=3600, token_status=200, token_body=None, discovery_body=None,
           discovery_status=200, delay: float = 0.0):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            calls["discovery"] = calls.get("discovery", 0) + 1
            body = discovery_body if discovery_body is not None else {"token_endpoint": f"{ISSUER}/oauth/token"}
            return httpx.Response(discovery_status, json=body)
        if request.url.path == "/oauth/token":
            calls["token"] = calls.get("token", 0) + 1
            calls.setdefault("requests", []).append(request)
            if delay:
                await asyncio.sleep(delay)
            if token_body is not None:
                return httpx.Response(token_status, **token_body)
            return httpx.Response(token_status, json={"access_token": f"async-token-{calls['token']}",
                                                      "token_type": "Bearer", "expires_in": expires_in})
        return httpx.Response(404)
    return handler


def provider(calls: dict, clock: Clock | None = None, **kwargs) -> AsyncOIDCClientCredentialsAuth:
    options = {k: kwargs.pop(k) for k in ("token_endpoint", "scope", "leeway_seconds") if k in kwargs}
    return AsyncOIDCClientCredentialsAuth(
        None if "token_endpoint" in options else ISSUER, client_id="worker", client_secret="s3cret",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(issuer(calls, **kwargs))),
        clock=clock or Clock(), **options,
    )


async def test_discovery_fetch_scope_and_basic_auth() -> None:
    calls: dict = {}
    auth = provider(calls, scope="operator")
    assert await auth.authorization_header_async() == "Bearer async-token-1"
    request = calls["requests"][0]
    assert request.headers["Authorization"] == "Basic " + base64.b64encode(b"worker:s3cret").decode()
    assert dict(httpx.QueryParams(request.content.decode())) == {"grant_type": "client_credentials", "scope": "operator"}
    assert calls["discovery"] == 1 and calls["token"] == 1


async def test_explicit_token_endpoint_skips_discovery() -> None:
    calls: dict = {}
    auth = provider(calls, token_endpoint=f"{ISSUER}/oauth/token")
    assert await auth.access_token() == "async-token-1"
    assert "discovery" not in calls


async def test_caches_until_leeway_then_refreshes_and_rediscovery_is_skipped() -> None:
    calls: dict = {}
    clock = Clock()
    auth = provider(calls, clock, expires_in=600, leeway_seconds=60)
    assert await auth.access_token() == "async-token-1"
    clock.now += 539
    assert await auth.access_token() == "async-token-1"
    clock.now += 1  # now exactly leeway seconds before expiry
    assert await auth.access_token() == "async-token-2"
    assert calls["discovery"] == 1 and calls["token"] == 2
    auth.refresh()
    assert await auth.access_token() == "async-token-3"


async def test_concurrent_callers_share_one_fetch() -> None:
    calls: dict = {}
    auth = provider(calls, delay=0.05)
    headers = await asyncio.gather(*(auth.authorization_header_async() for _ in range(25)))
    assert set(headers) == {"Bearer async-token-1"}
    assert calls["token"] == 1 and calls["discovery"] == 1


@pytest.mark.parametrize(("kwargs", "error", "match"), [
    ({"discovery_status": 500}, AuthenticationError, "discovery failed with status 500"),
    ({"discovery_body": {"issuer": "x"}}, AuthenticationError, "no token_endpoint"),
    ({"discovery_body": {"token_endpoint": "ftp://x"}}, AuthenticationError, "invalid token_endpoint"),
    ({"token_status": 401, "token_body": {"json": {"error": "invalid_client", "error_description": "bad secret"}}},
     AuthenticationError, "invalid_client bad secret"),
    ({"token_body": {"content": b"<html>"}}, AuthenticationError, "non-JSON"),
    ({"token_body": {"json": {"token_type": "Bearer"}}}, AuthenticationError, "no access_token"),
    ({"token_body": {"json": {"access_token": "t", "token_type": "mac"}}}, AuthenticationError, "unsupported token_type"),
    ({"token_body": {"json": {"access_token": "t", "expires_in": "soon"}}}, AuthenticationError, "non-numeric"),
    ({"token_body": {"json": {"access_token": "t", "expires_in": 0}}}, MeemeeError, "non-positive"),
])
async def test_error_modes_match_the_sync_provider(kwargs, error, match) -> None:
    with pytest.raises(error, match=match):
        await provider({}, **kwargs).access_token()


async def test_transport_failures_are_authentication_errors() -> None:
    async def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    for kwargs in ({"issuer": ISSUER}, {"token_endpoint": f"{ISSUER}/oauth/token"}):
        auth = AsyncOIDCClientCredentialsAuth(
            kwargs.get("issuer"), token_endpoint=kwargs.get("token_endpoint"), client_id="w", client_secret="s",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(down)),
        )
        with pytest.raises(AuthenticationError, match="request failed"):
            await auth.access_token()


def test_constructor_validation_and_sync_misuse() -> None:
    with pytest.raises(ValueError):
        AsyncOIDCClientCredentialsAuth(client_id="w", client_secret="s")
    with pytest.raises(ValueError):
        AsyncOIDCClientCredentialsAuth("idp.test", client_id="w", client_secret="s")
    with pytest.raises(ValueError):
        AsyncOIDCClientCredentialsAuth(ISSUER, client_id="", client_secret="s")
    with pytest.raises(ValueError):
        AsyncOIDCClientCredentialsAuth(ISSUER, client_id="w", client_secret="")
    with pytest.raises(ValueError):
        AsyncOIDCClientCredentialsAuth(ISSUER, client_id="w", client_secret="s", leeway_seconds=-1)
    auth = AsyncOIDCClientCredentialsAuth(ISSUER, client_id="w", client_secret="s")
    sync_client = MeemeeClient("http://api.test", auth=auth,
                               transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    with pytest.raises(TypeError, match="asyncio-only"):
        sync_client.jobs.list()


async def test_async_client_awaits_native_provider_without_thread_offload(monkeypatch) -> None:
    async def no_threads(*args, **kwargs):  # pragma: no cover - failing path
        raise AssertionError("native async provider must not be offloaded to a thread")

    calls: dict = {}
    auth = provider(calls)
    seen: list[str] = []

    def api(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, json={"jobs": [], "next_cursor": None})

    monkeypatch.setattr(asyncio, "to_thread", no_threads)
    async with AsyncMeemeeClient("http://api.test", auth=auth, transport=httpx.MockTransport(api)) as client:
        await asyncio.gather(*(client.jobs.list() for _ in range(10)))
    assert seen == ["Bearer async-token-1"] * 10 and calls["token"] == 1


async def test_sync_provider_still_works_through_thread_fallback() -> None:
    def sync_issuer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={"token_endpoint": f"{ISSUER}/oauth/token"})
        return httpx.Response(200, json={"access_token": "sync-token", "token_type": "Bearer", "expires_in": 600})

    sync_auth = OIDCClientCredentialsAuth(ISSUER, client_id="w", client_secret="s",
                                          http_client=httpx.Client(transport=httpx.MockTransport(sync_issuer)))
    seen: list[str] = []

    def api(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, json={"jobs": [], "next_cursor": None})

    async with AsyncMeemeeClient("http://api.test", auth=sync_auth, transport=httpx.MockTransport(api)) as client:
        await client.jobs.list()
    assert seen == ["Bearer sync-token"]


async def test_aclose_closes_only_owned_http_client() -> None:
    owned = AsyncOIDCClientCredentialsAuth(ISSUER, client_id="w", client_secret="s")
    await owned.aclose()
    assert owned._http.is_closed
    shared = httpx.AsyncClient()
    borrowed = AsyncOIDCClientCredentialsAuth(ISSUER, client_id="w", client_secret="s", http_client=shared)
    await borrowed.aclose()
    assert not shared.is_closed
    await shared.aclose()
