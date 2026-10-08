"""Failed PG composition cannot abandon its already-open pool."""

import pytest
from test_token_audit_backends import PG_DSN, _pg_dsn

import meemee_persist_pg as pg
from meemee.persistence import build_persistence


@pytest.mark.skipif(not PG_DSN, reason='requires real PostgreSQL')
@pytest.mark.parametrize('failure', ['migration', 'quota'])
def test_pg_composition_failure_closes_actual_pool(monkeypatch, tmp_path, failure):
    dsn, drop = _pg_dsn()
    pools = []
    original = pg.Database

    def capture(*args, **kwargs):
        db = original(*args, **kwargs)
        pools.append(db.pool)
        return db

    monkeypatch.setattr(pg, 'Database', capture)
    if failure == 'migration':
        def broken(*args):
            raise RuntimeError('migration failed')
        monkeypatch.setattr(pg.MigrationStore, 'apply', broken)
    try:
        with pytest.raises((RuntimeError, ValueError)):
            build_persistence('postgresql', tmp_path, dsn, default_daily_jobs=True if failure == 'quota' else 100)
        assert pools[0].closed
    finally:
        for pool in pools:
            pool.close()
        drop()


def test_actual_pool_initialization_timeout_already_releases_workers(monkeypatch):
    from psycopg_pool import PoolTimeout

    from meemee_persist_pg import _db

    pools = []
    original = _db.ConnectionPool

    def capture(*args, **kwargs):
        pool = original(*args, **kwargs)
        pools.append(pool)
        return pool

    monkeypatch.setattr(_db, 'ConnectionPool', capture)
    with pytest.raises(PoolTimeout):
        _db.Database('host=127.0.0.1 port=1 user=sandbox dbname=postgres connect_timeout=1', timeout=.1)
    assert pools[0].closed
