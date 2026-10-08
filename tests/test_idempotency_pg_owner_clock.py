"""Owned PG publication uses the database clock that created the pending lease."""

from datetime import datetime, timedelta, timezone

import pytest
from test_token_audit_backends import PG_DSN, _pg_dsn

from meemee.idempotency import IdempotencyConflict
from meemee_persist_pg import Database, IdempotencyStore, MigrationStore
from meemee_persist_pg import idempotency as adapter


@pytest.mark.skipif(not PG_DSN, reason='requires real PostgreSQL')
@pytest.mark.parametrize('client_offset', [-86400, 86400])
def test_pg_owned_expiry_ignores_client_clock_skew(monkeypatch, client_offset):
    dsn, drop = _pg_dsn()
    db = Database(dsn)
    try:
        MigrationStore(db).apply()
        store = IdempotencyStore(db)
        store.claim('owner', '/jobs', 'key', {}, claim_token='fresh')

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(timezone.utc) + timedelta(seconds=client_offset)

        monkeypatch.setattr(adapter, 'datetime', Clock)
        if client_offset < 0:
            with db.transaction() as c:
                c.execute("UPDATE meemee_idempotency SET expires_at=clock_timestamp()-interval '1 second'")
            with pytest.raises(IdempotencyConflict):
                store.put('owner', '/jobs', 'key', {}, 200, {'id': 'expired'}, claim_token='fresh')
        else:
            store.put('owner', '/jobs', 'key', {}, 200, {'id': 'live'}, claim_token='fresh')
            assert store.get('owner', '/jobs', 'key', {}) == (200, {'id': 'live'})
    finally:
        db.close()
        drop()
