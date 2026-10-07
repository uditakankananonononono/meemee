"""Retention cannot erase the audit events of a job it fails to remove."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from meemee.jobs import JobStore
from meemee.retention import RetentionManager


def test_terminal_retention_rolls_back_events_on_job_delete_failure(tmp_path):
    jobs = JobStore(tmp_path / 'jobs.sqlite3')
    old = jobs.enqueue('old')
    jobs.claim()
    jobs.finish(old, {'done': True})
    active = jobs.enqueue('active')
    past = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    jobs.db.execute('UPDATE jobs SET updated_at=? WHERE id=?', (past, old))
    jobs.db.executescript("""
        CREATE TRIGGER refuse_terminal_delete BEFORE DELETE ON jobs
        BEGIN SELECT RAISE(ABORT, 'injected job deletion failure'); END;
    """)
    before = jobs.events(old)
    with pytest.raises(sqlite3.IntegrityError, match='injected'):
        RetentionManager(tmp_path).run()
    assert jobs.events(old) == before
    assert jobs.get(old)['status'] == 'done'
    jobs.db.execute('DROP TRIGGER refuse_terminal_delete')
    report = RetentionManager(tmp_path).run()
    assert report.terminal_jobs == 1
    assert report.job_events == len(before)
    assert jobs.get(active)['status'] == 'queued'
    assert len(jobs.events(active)) == 1
