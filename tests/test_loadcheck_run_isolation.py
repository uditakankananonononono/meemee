"""A loadcheck's census must not include jobs from previous runs."""

from meemee.jobs import JobStore
from meemee.loadcheck import run_loadcheck


def test_repeated_loadchecks_use_distinct_job_namespaces(tmp_path):
    existing = JobStore(tmp_path / 'jobs.sqlite3')
    prior = existing.enqueue('unrelated', principal='loadcheck')
    first = run_loadcheck(operations=8, workers=2, data_dir=tmp_path)
    second = run_loadcheck(operations=8, workers=2, data_dir=tmp_path)
    assert first['status'] == second['status'] == 'pass'
    assert first['stored_jobs'] == second['stored_jobs'] == 2
    assert first['principal'] != second['principal']
    assert existing.get(prior)['goal'] == 'unrelated'
