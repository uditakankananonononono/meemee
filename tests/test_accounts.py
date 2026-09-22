from pathlib import Path

from fastapi.testclient import TestClient

from meemee import api
from meemee.auth import TokenStore


def test_password_account_signup_login_and_secret_storage(tmp_path: Path):
    store = TokenStore(tmp_path / "accounts.db")
    account, token = store.create_account("User@Example.com", "correct horse battery", "Ada")
    assert account["email"] == "user@example.com"
    assert b"correct horse battery" not in (tmp_path / "accounts.db").read_bytes()
    assert store.authenticate(token).id == account["id"]
    assert store.login_account("USER@example.com", "wrong password") is None
    assert store.login_account("user@example.com", "correct horse battery")[0]["id"] == account["id"]


def test_self_serve_flow_and_owner_scoped_keys(monkeypatch, tmp_path: Path):
    store = TokenStore(tmp_path / "accounts.db")
    monkeypatch.setattr(api, "tokens", store)
    monkeypatch.setattr(api.auth, "store", store)
    client = TestClient(api.app)
    signed = client.post("/v1/accounts/signup", json={"email": "ada@example.com", "password": "correct horse battery", "display_name": "Ada"})
    assert signed.status_code == 201
    bearer = {"Authorization": f"Bearer {signed.json()['token']}"}
    assert client.get("/v1/account", headers=bearer).json()["email"] == "ada@example.com"
    created = client.post("/v1/account/api-keys", headers=bearer, json={"name": "notes", "scopes": ["companion:read"]})
    assert created.status_code == 201 and created.json()["token"].startswith("mee_")
    listing = client.get("/v1/account/api-keys", headers=bearer).json()["api_keys"]
    assert [item["name"] for item in listing] == ["notes"]
    assert client.delete(f"/v1/account/api-keys/{created.json()['id']}", headers=bearer).status_code == 200


def test_signup_rejects_weak_password_and_duplicate_email(tmp_path: Path):
    store = TokenStore(tmp_path / "accounts.db")
    try:
        store.create_account("x@example.com", "too-short", "X")
        raise AssertionError("weak password accepted")
    except ValueError as exc:
        assert "12-200" in str(exc)
    store.create_account("x@example.com", "long enough password", "X")
    try:
        store.create_account("X@example.com", "another long password", "X")
        raise AssertionError("duplicate accepted")
    except ValueError as exc:
        assert "already exists" in str(exc)
