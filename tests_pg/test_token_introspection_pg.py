"""Live PostgreSQL token introspection. Run with MEEMEE_TEST_POSTGRES_DSN pointed at an expendable database."""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.skipif(not os.getenv("MEEMEE_TEST_POSTGRES_DSN"), reason="requires real PostgreSQL")


def test_pg_introspect_matches_sqlite_semantics():
    from meemee_persist_pg import Database, MigrationStore, TokenStore
    db = Database(os.environ["MEEMEE_TEST_POSTGRES_DSN"], min_size=1, max_size=2)
    MigrationStore(db).apply()
    store = TokenStore(db)
    ident, secret = store.create(f"pg-{uuid.uuid4().hex[:6]}", {"jobs:write", "jobs:read"})
    record = store.introspect(secret)
    assert record["active"] is True and record["state"] == "active" and record["id"] == ident
    assert record["scopes"] == ["jobs:read", "jobs:write"] and record["principal"] == ident
    assert record["last_used_at"] is None and secret not in repr(record) and "digest" not in record
    assert store.authenticate(secret) is not None
    assert store.introspect(secret)["last_used_at"] is not None
    assert store.revoke(ident)
    assert store.introspect(secret)["state"] == "revoked"
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    _, old = store.create(f"pg-old-{uuid.uuid4().hex[:6]}", {"jobs:read"}, expires_at=past)
    assert store.introspect(old)["state"] == "expired" and store.authenticate(old) is None
    assert store.introspect(f"mee_{uuid.uuid4().hex}") is None
