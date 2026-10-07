"""Native store errors count as failed operations instead of aborting the gate."""

import sqlite3

from meemee.jobs import JobStore
from meemee.loadcheck import run_loadcheck


def test_loadcheck_handles_native_sqlite_error(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError('private fixture detail')

    monkeypatch.setattr(JobStore, 'enqueue', broken)
    result = run_loadcheck(operations=8, workers=2, data_dir=tmp_path)
    assert result['status'] == 'fail'
    assert result['errors'] == ['OperationalError'] * 2
    assert sum(result['counts'].values()) == 6
    assert 'private fixture detail' not in str(result)
