"""tokens.introspect on the sync and async clients against a mocked transport."""
from __future__ import annotations

import json

import httpx
import pytest
from conftest import BASE_URL, make_client
from meemee_client import AsyncMeemeeClient, PermissionDeniedError, TokenIntrospection

ACTIVE = {
    "redacted_token": "mee_...wxyz", "active": True, "state": "active", "credential": "api_token",
    "id": "t1", "name": "ci", "scopes": ["jobs:read"], "principal": "t1", "token_kind": "api",
    "created_at": "2026-09-24T00:00:00+00:00", "last_used_at": None,
    "expires_at": "2026-10-01T00:00:00+00:00", "revoked_at": None,
}
UNKNOWN = {"redacted_token": "mee_...0000", "active": False, "state": "unknown", "credential": None, "scopes": []}


def handler_for(seen: list[httpx.Request], body: dict, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=body)
    return handler


def test_sync_introspect_posts_secret_in_body_only() -> None:
    seen: list[httpx.Request] = []
    result = make_client(handler_for(seen, ACTIVE)).tokens.introspect("mee_secretvaluewxyz")
    assert isinstance(result, TokenIntrospection)
    assert result.active and result.state == "active" and result.scopes == ["jobs:read"]
    assert result.expires_at is not None and result.expires_at.year == 2026
    request = seen[0]
    assert request.method == "POST" and request.url.path == "/v1/tokens/introspect"
    assert "mee_secretvaluewxyz" not in str(request.url)
    assert json.loads(request.content) == {"token": "mee_secretvaluewxyz"}


def test_sync_introspect_unknown_revoked_and_validation() -> None:
    seen: list[httpx.Request] = []
    unknown = make_client(handler_for(seen, UNKNOWN)).tokens.introspect("mee_x")
    assert unknown.active is False and unknown.state == "unknown" and unknown.credential is None and unknown.id is None
    revoked = make_client(handler_for(seen, {**ACTIVE, "active": False, "state": "revoked",
                                              "revoked_at": "2026-09-24T01:00:00+00:00"})).tokens.introspect("mee_y")
    assert revoked.state == "revoked" and revoked.revoked_at is not None
    client = make_client(handler_for(seen, UNKNOWN))
    for bad in ("", "   ", "x" * 4097):
        with pytest.raises(ValueError):
            client.tokens.introspect(bad)


def test_sync_introspect_scope_denial_is_typed() -> None:
    seen: list[httpx.Request] = []
    client = make_client(handler_for(seen, {"detail": "missing scope: admin"}, 403))
    with pytest.raises(PermissionDeniedError) as caught:
        client.tokens.introspect("mee_z")
    assert caught.value.missing_scope == "admin"


async def test_async_introspect_matches_sync() -> None:
    seen: list[httpx.Request] = []
    client = AsyncMeemeeClient(BASE_URL, auth="mee_admin", transport=httpx.MockTransport(handler_for(seen, ACTIVE)))
    result = await client.tokens.introspect("mee_secretvaluewxyz")
    assert result == make_client(handler_for([], ACTIVE)).tokens.introspect("mee_secretvaluewxyz")
    assert json.loads(seen[0].content) == {"token": "mee_secretvaluewxyz"}
    with pytest.raises(ValueError):
        await client.tokens.introspect("")
    await client.aclose()
