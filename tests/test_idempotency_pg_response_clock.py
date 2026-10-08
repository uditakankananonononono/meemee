"""Completed PG response retention starts on the same DB clock as replay cleanup."""

from datetime import datetime, timedelta, timezone

import pytest
from test_token_audit_backends import PG_DSN, _pg_dsn

from meemee_persist_pg import Database, IdempotencyStore, MigrationStore
from meemee_persist_pg import idempotency as adapter


@pytest.mark.skipif(not PG_DSN, reason='requires real PostgreSQL')
@pytest.mark.parametrize('client_offset', [-172800, 172800])
def test_pg_response_ttl_uses_database_clock(monkeypatch, client_offset):
    dsn, drop = _pg_dsn()
    db = Database(dsn)
    try:
        MigrationStore(db).apply()
        store = IdempotencyStore(db)

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(timezone.utc) + timedelta(seconds=client_offset)

        monkeypatch.setattr(adapter, 'datetime', Clock, raising=False)
        store.put('owner', '/jobs', 'key', {}, 200, {'id': 'job'})
        with db.transaction() as c:
            row = c.execute('SELECT created_at,expires_at,clock_timestamp() AS now FROM meemee_idempotency').fetchone()
        assert abs((row['created_at'] - row['now']).total_seconds()) < 2
        assert abs((row['expires_at'] - row['now'] - store.ttl).total_seconds()) < 2
        assert store.get('owner', '/jobs', 'key', {}) == (200, {'id': 'job'})
    finally:
        db.close()
        drop()
