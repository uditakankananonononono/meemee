"""Real PG source deletion cannot race context publication."""

import pytest
from test_token_audit_backends import PG_DSN, _pg_dsn

from meemee.context import ContextRecord


@pytest.mark.skipif(not PG_DSN, reason='requires real PostgreSQL')
def test_pg_ingest_does_not_publish_after_preflight_source_deletion(monkeypatch):
    from meemee_persist_pg import ContextStore, Database, MigrationStore

    dsn, drop = _pg_dsn()
    db = Database(dsn)
    MigrationStore(db).apply()
    try:
        store = ContextStore(db)
        store.register_source('owner', 'source', 'test', {})
        original = store.source

        def removed_after_read(owner, source):
            result = original(owner, source)
            with db.transaction() as c:
                c.execute('DELETE FROM meemee_context_sources WHERE owner_id=%s AND source_id=%s', (owner, source))
            return result

        monkeypatch.setattr(store, 'source', removed_after_read)
        record = ContextRecord('owner', 'source', 'event', 'event', 'title', 'body', '2026-10-08', {})
        try:
            store.ingest(record)
        except ValueError:
            pass
        with db.transaction() as c:
            orphan = c.execute('''SELECT 1 FROM meemee_context_records r WHERE NOT EXISTS
                (SELECT 1 FROM meemee_context_sources s WHERE s.owner_id=r.owner_id AND s.source_id=r.source_id)''').fetchone()
        assert orphan is None
    finally:
        db.close()
        drop()


@pytest.mark.skipif(not PG_DSN, reason='requires real PostgreSQL')
def test_pg_source_lock_and_missing_source_rollback(monkeypatch):
    from contextlib import contextmanager

    import psycopg

    from meemee_persist_pg import ContextStore, Database, MigrationStore

    dsn, drop = _pg_dsn()
    db = Database(dsn)
    MigrationStore(db).apply()
    try:
        store = ContextStore(db)
        store.register_source('owner', 'source', 'test', {})
        transaction = db.transaction
        observed = []

        class CheckedConnection:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, sql, args=()):
                result = self.connection.execute(sql, args)
                if 'FOR UPDATE' in sql and 'meemee_context_sources' in sql:
                    with psycopg.connect(dsn) as other:
                        other.execute("SET lock_timeout='50ms'")
                        with pytest.raises(psycopg.errors.LockNotAvailable):
                            other.execute('DELETE FROM meemee_context_sources WHERE owner_id=%s AND source_id=%s', ('owner', 'source'))
                    observed.append(True)
                return result

        @contextmanager
        def checked_transaction():
            with transaction() as c:
                yield CheckedConnection(c)

        monkeypatch.setattr(db, 'transaction', checked_transaction)
        record = ContextRecord('owner', 'source', 'event', 'event', 'title', 'body', '2026-10-08', {})
        assert store.ingest(record)
        assert observed == [True]
        monkeypatch.setattr(db, 'transaction', transaction)
        with transaction() as c:
            c.execute('DELETE FROM meemee_context_sources WHERE owner_id=%s', ('owner',))
        with pytest.raises(ValueError, match='source is not registered'):
            store.ingest(ContextRecord('owner', 'source', 'missing', 'event', 'title', 'body', '2026-10-08', {}))
        with transaction() as c:
            assert c.execute('SELECT count(*) n FROM meemee_context_records').fetchone()['n'] == 1
    finally:
        db.close()
        drop()
