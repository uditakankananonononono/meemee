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


def test_login_locks_for_fifteen_minutes_after_five_failures(tmp_path: Path):
    store = TokenStore(tmp_path / "accounts.db")
    store.create_account("lock@example.com", "correct horse battery", "Lock")
    for _ in range(5):
        assert store.login_account("lock@example.com", "wrong password value") is None
    assert store.login_account("lock@example.com", "correct horse battery") is None
    row = store.db.execute("SELECT failed_logins,locked_until FROM accounts WHERE email='lock@example.com'").fetchone()
    assert row["failed_logins"] == 5 and row["locked_until"]

def test_email_bridge_status_exposes_gmail_connection_requirement(monkeypatch, tmp_path: Path):
    store = TokenStore(tmp_path / "bridge-accounts.db")
    monkeypatch.setattr(api, "tokens", store)
    monkeypatch.setattr(api.auth, "store", store)
    client = TestClient(api.app)
    token=client.post('/v1/accounts/signup',json={'email':'bridge@example.com','password':'correct horse battery','display_name':'Bridge'}).json()['token']
    response=client.get('/v1/email-bridge/status',headers={'Authorization':f'Bearer {token}'})
    assert response.status_code==200
    assert response.json()['gmail_connection_required'] is True
    assert response.json()['gmail_required_scope'].endswith('gmail.readonly')


def test_read_only_key_cannot_mint_write_credentials(monkeypatch, tmp_path: Path):
    store = TokenStore(tmp_path / "key-escalation.db")
    monkeypatch.setattr(api, "tokens", store)
    monkeypatch.setattr(api.auth, "store", store)
    _, raw = store.create("reader", {"jobs:read"}, owner_id="owner")
    client = TestClient(api.app)
    response = client.post("/v1/account/api-keys", headers={"Authorization": f"Bearer {raw}"},
                           json={"name": "escalated", "scopes": ["runs:write", "jobs:write", "companion:write"]})
    assert response.status_code == 403
    assert "token" not in response.json()
    assert store.list_metadata(owner_id="owner")[0][0]["name"] == "reader"
    assert len(store.list_metadata(owner_id="owner")[0]) == 1


def test_account_keys_cannot_amplify_writer_scopes(monkeypatch, tmp_path: Path):
    store = TokenStore(tmp_path / "key-subsets.db")
    monkeypatch.setattr(api, "tokens", store)
    monkeypatch.setattr(api.auth, "store", store)
    _, raw = store.create("writer", {"jobs:read", "jobs:write"}, owner_id="owner")
    client = TestClient(api.app)
    headers = {"Authorization": f"Bearer {raw}"}
    denied = client.post("/v1/account/api-keys", headers=headers,
                        json={"name": "amplified", "scopes": ["runs:write", "companion:write"]})
    assert denied.status_code == 403 and "token" not in denied.json()
    allowed = client.post("/v1/account/api-keys", headers=headers,
                         json={"name": "subset", "scopes": ["jobs:read"]})
    assert allowed.status_code == 201
    minted = store.authenticate(allowed.json()["token"])
    assert minted.id == "owner" and minted.scopes == frozenset({"jobs:read"})
    assert client.post("/v1/jobs", headers={"Authorization": f"Bearer {allowed.json()['token']}"},
                       json={"goal": "should not run"}).status_code == 403
