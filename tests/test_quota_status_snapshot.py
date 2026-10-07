"""Quota status reads limit and usage from one coherent state."""

from meemee.quotas import QuotaStore


def test_status_uses_single_snapshot_across_limit_usage(tmp_path, monkeypatch):
    path = tmp_path / 'quota.sqlite3'
    reader, writer = QuotaStore(path), QuotaStore(path)
    writer.set_limit('owner', 10)
    original = reader.limit

    def changed_after_limit(owner):
        result = original(owner)
        writer.set_limit(owner, 20)
        writer.consume_job(owner)
        return result

    monkeypatch.setattr(reader, 'limit', changed_after_limit)
    status = reader.status('owner')
    assert (status['limit'], status['used']) in {(10, 0), (20, 1)}


def test_pg_status_uses_single_snapshot(monkeypatch):
    import pytest
    from test_token_audit_backends import PG_DSN, _pg_dsn

    if not PG_DSN:
        pytest.skip('requires real PostgreSQL')
    from meemee_persist_pg import Database, MigrationStore
    from meemee_persist_pg.quotas import QuotaStore as PGQuota

    dsn, drop = _pg_dsn()
    db = Database(dsn)
    MigrationStore(db).apply()
    try:
        reader, writer = PGQuota(db), PGQuota(db)
        writer.set_limit('owner', 10)
        original = reader._limit

        def changed_after_limit(connection, owner):
            result = original(connection, owner)
            writer.set_limit(owner, 20)
            writer.consume_job(owner)
            return result

        monkeypatch.setattr(reader, '_limit', changed_after_limit)
        status = reader.status('owner')
        assert (status['limit'], status['used']) in {(10, 0), (20, 1)}
    finally:
        db.close()
        drop()
