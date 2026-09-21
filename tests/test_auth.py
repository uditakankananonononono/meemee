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
