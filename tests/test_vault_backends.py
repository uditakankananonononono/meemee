"""Secret vault and key rotation: SQLite and PostgreSQL."""
from __future__ import annotations

import os

import pytest
from test_token_audit_backends import BACKENDS, _pg_dsn

from meemee.vault import SecretVault as SQLiteVault

OLD = SQLiteVault.generate_key()
NEW = SQLiteVault.generate_key()


@pytest.fixture(params=BACKENDS)
def vault(request, tmp_path):
    if request.param == "sqlite":
        yield lambda key: SQLiteVault(tmp_path / "vault.sqlite3", key)
        return
    from meemee_persist_pg import Database, MigrationStore, SecretVault

    dsn, drop = _pg_dsn()
    db = Database(dsn, min_size=1, max_size=4)
    MigrationStore(db).apply()
    try:
        yield lambda key: SecretVault(db, key)
    finally:
        db.close(); drop()


def test_vault_contract(vault):
    v = vault(OLD)
    assert v.ping()
    with pytest.raises(ValueError):
        v.put("x", "")
    v.put("b", "two"); v.put("a", "one"); v.put("a", "uno")
    assert v.get("a") == "uno" and v.names() == ["a", "b"] and '"count": 2' in v.export_metadata()
    with pytest.raises(KeyError):
        v.get("missing")
    with pytest.raises(Exception):  # noqa: B017 - wrong key fails authentication
        vault(NEW).get("a")


def test_pg_rotation_reencrypts_vault_and_webhook_secrets():
    if not (os.getenv("MEEMEE_TEST_POSTGRES_DSN") or os.getenv("MEEMEE_TEST_DATABASE_URL")):
        pytest.skip("requires real PostgreSQL")
    from meemee_persist_pg import (
        Database,
        MigrationStore,
        SecretVault,
        WebhookStore,
        rotate_pg_keys,
    )

    dsn, drop = _pg_dsn()
    db = Database(dsn, min_size=1, max_size=4)
    try:
        MigrationStore(db).apply()
        SecretVault(db, OLD).put("api", "s3cret")
        hooks = WebhookStore(db, 256_000, OLD)
        ident, secret = hooks.subscribe("p", "https://example.com/hook", {"job.completed"})
        assert rotate_pg_keys(db, OLD, NEW) == {"vault_secrets": 1, "webhook_secrets": 1}
        assert SecretVault(db, NEW).get("api") == "s3cret"
        with pytest.raises(Exception):  # noqa: B017
            SecretVault(db, OLD).get("api")
        rotated = WebhookStore(db, 256_000, NEW)  # startup check decrypts stored secrets with the new key
        with pytest.raises(Exception):  # noqa: B017
            WebhookStore(db, 256_000, OLD)
        rotated.enqueue("e1", "job.completed", {"ok": True}, principal="p")
        assert rotated.claim()["secret"] == secret and ident
        # a failed rotation (wrong old key) changes nothing
        with pytest.raises(Exception):  # noqa: B017
            rotate_pg_keys(db, OLD, NEW)
        assert SecretVault(db, NEW).get("api") == "s3cret"
    finally:
        db.close(); drop()
