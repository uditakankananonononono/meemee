from pathlib import Path

from meemee.auth import TokenStore


def test_scoped_token_lifecycle(tmp_path: Path):
    store = TokenStore(tmp_path / "auth.db")
    ident, raw = store.create("worker", {"jobs:read", "jobs:write"})
    assert raw.startswith("mee_")
    assert raw.encode() not in (tmp_path / "auth.db").read_bytes()
    principal = store.authenticate(raw)
    assert principal and principal.scopes == {"jobs:read", "jobs:write"}
    assert store.revoke(ident)
    assert store.authenticate(raw) is None
    assert not store.revoke(ident)


def test_expired_token_is_rejected(tmp_path: Path):
    store = TokenStore(tmp_path / "auth.db")
    _, raw = store.create("old", {"jobs:read"}, "2020-01-01T00:00:00+00:00")
    assert store.authenticate(raw) is None


def test_token_metadata_listing_never_exposes_digest_or_secret(tmp_path: Path):
    store=TokenStore(tmp_path/"auth.db")
    ident, raw=store.create("ci",{"jobs:read"})
    rows=store.list_metadata(revoked=False)[0]
    assert rows[0]["id"]==ident and rows[0]["scopes"]==["jobs:read"]
    assert "digest" not in rows[0] and raw not in str(rows)
    store.revoke(ident)
    assert store.list_metadata(revoked=False)[0]==[]
    assert store.list_metadata(revoked=True)[0][0]["revoked_at"]


def test_token_list_api_returns_metadata_only(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from meemee import api
    store=TokenStore(tmp_path/"api-auth.db"); _,raw=store.create("safe",{"jobs:read"})
    monkeypatch.setattr(api,"tokens",store)
    response=TestClient(api.app).get("/v1/tokens",headers={"Authorization":"Bearer test-bootstrap-token"})
    assert response.status_code==200
    body=response.json(); assert body["tokens"][0]["name"]=="safe"
    assert "digest" not in str(body) and raw not in str(body)
