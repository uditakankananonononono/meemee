"""Pending PG lease duration begins after a delayed insert finishes."""

from datetime import timedelta

import pytest
from test_token_audit_backends import PG_DSN, _pg_dsn

from meemee.idempotency import IdempotencyInProgress
from meemee_persist_pg import Database, IdempotencyStore, MigrationStore
from meemee_persist_pg import idempotency as adapter


@pytest.mark.skipif(not PG_DSN, reason='requires real PostgreSQL')
def test_pg_claim_lease_starts_after_insert_delay(monkeypatch):
    dsn, drop = _pg_dsn()
    db = Database(dsn)
    try:
        MigrationStore(db).apply()
        monkeypatch.setattr(adapter, 'CLAIM_LEASE', timedelta(seconds=.5))
        with db.transaction() as c:
            c.execute("CREATE FUNCTION slow_claim() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN PERFORM pg_sleep(.6); RETURN NEW; END; $$")
            c.execute('CREATE TRIGGER delay_claim BEFORE INSERT ON meemee_idempotency FOR EACH ROW EXECUTE FUNCTION slow_claim()')
        store = IdempotencyStore(db)
        assert store.claim('owner', '/jobs', 'key', {}, claim_token='new') is None
        with db.transaction() as c:
            row = c.execute('SELECT expires_at,clock_timestamp() AS now FROM meemee_idempotency').fetchone()
        assert (row['expires_at'] - row['now']).total_seconds() > .3
        with pytest.raises(IdempotencyInProgress):
            store.get('owner', '/jobs', 'key', {})
    finally:
        db.close()
        drop()
