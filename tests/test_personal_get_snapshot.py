"""An item and its evidence must represent one database snapshot."""

from meemee.personal_model import PersonalItemInput, PersonalModelStore


def test_sqlite_personal_get_snapshot(tmp_path):
    path = tmp_path / 'personal.sqlite3'
    reader, writer = PersonalModelStore(path), PersonalModelStore(path)
    def value(confidence, source):
        return PersonalItemInput(kind='preference', title='drink', value='tea', confidence=confidence,
                                 source_id=source, source_record_id='record')
    row = writer.upsert('owner', value(0.2, 'first'))
    changed = []
    def race(sql):
        if 'SELECT source_id,source_record_id' in sql and not changed:
            changed.append(True)
            writer.upsert('owner', value(0.9, 'second'))
    reader.db.set_trace_callback(race)
    result = reader.get('owner', row['id'])
    assert result['confidence'] == max(e['confidence'] for e in result['evidence'])


def test_pg_personal_get_snapshot(monkeypatch):
    from contextlib import contextmanager

    import pytest
    from test_token_audit_backends import PG_DSN, _pg_dsn

    if not PG_DSN:
        pytest.skip('requires real PostgreSQL')
    from meemee_persist_pg import Database, MigrationStore
    from meemee_persist_pg import PersonalModelStore as PGPersonal

    dsn, drop = _pg_dsn()
    db = Database(dsn)
    MigrationStore(db).apply()
    try:
        reader, writer = PGPersonal(db), PGPersonal(db)
        def value(confidence, source):
            return PersonalItemInput(kind='preference', title='drink', value='tea', confidence=confidence,
                                     source_id=source, source_record_id='record')
        row = writer.upsert('owner', value(0.2, 'first'))
        transaction = db.transaction
        changed = []
        class Proxy:
            def __init__(self, c):
                self.c = c
            def execute(self, sql, args=()):
                if 'SELECT source_id,source_record_id' in sql and not changed:
                    changed.append(True)
                    writer.upsert('owner', value(0.9, 'second'))
                return self.c.execute(sql, args)
        @contextmanager
        def raced_transaction(**kw):
            with transaction(**kw) as c:
                yield Proxy(c)
        monkeypatch.setattr(db, 'transaction', raced_transaction)
        result = reader.get('owner', row['id'])
        assert result['confidence'] == max(e['confidence'] for e in result['evidence'])
    finally:
        db.close()
        drop()
