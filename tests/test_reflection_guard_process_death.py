"""Real PG session reflection guards exclude another process and release on death."""

import os
import subprocess
import sys

import pytest
from test_token_audit_backends import PG_DSN, _pg_dsn


@pytest.mark.skipif(not PG_DSN, reason='requires real PostgreSQL')
def test_pg_reflection_guard_released_after_client_process_death():
    from meemee_persist_pg import Database, MigrationStore, ReflectionSchedule

    dsn, drop = _pg_dsn()
    db = Database(dsn)
    MigrationStore(db).apply()
    script = '''
import os, sys
from meemee_persist_pg import Database, ReflectionSchedule
db = Database(os.environ['GUARD_TEST_DSN'])
with ReflectionSchedule(db).owner_guard('owner') as claimed:
    print('CLAIMED' if claimed else 'BLOCKED', flush=True)
    sys.stdin.readline()
    os._exit(73)
'''
    child = None
    try:
        child = subprocess.Popen([sys.executable, '-c', script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, env={**os.environ, 'GUARD_TEST_DSN': dsn})
        assert child.stdout.readline().strip() == 'CLAIMED'
        schedule = ReflectionSchedule(db)
        with schedule.owner_guard('owner') as claimed:
            assert claimed is False
        with schedule.owner_guard('other-owner') as claimed:
            assert claimed is True
        child.stdin.write('exit\n')
        child.stdin.flush()
        assert child.wait(timeout=10) == 73
        with schedule.owner_guard('owner') as claimed:
            assert claimed is True
        with schedule.owner_guard('owner') as claimed:
            assert claimed is True
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.wait(timeout=10)
        db.close()
        drop()
