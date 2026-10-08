"""Run-list cutoff denotes an instant, not lexicographic local wall time."""

from test_token_audit_backends import persistence  # noqa: F401

from meemee.types import RunReport


def test_run_before_cutoff_normalizes_aware_offset(persistence):  # noqa: F811
    runs = persistence.runs
    runs.add('owner', RunReport(run_id='r1', goal='work', final='done', steps_used=1, tool_results=[]))
    stamp = '2026-10-08T04:00:00+00:00'
    if persistence.backend == 'sqlite':
        with runs.db:
            runs.db.execute('UPDATE runs SET created_at=? WHERE run_id=?', (stamp, 'r1'))
    else:
        with runs.db.transaction() as c:
            c.execute('UPDATE meemee_runs SET created_at=%s WHERE run_id=%s', (stamp, 'r1'))
    # 08:00IST is02:30UTC, earlier than this run, so it must be excluded.
    assert runs.list('owner', before='2026-10-08T08:00:00+05:30')[0] == []
    assert len(runs.list('owner', before='2026-10-08T10:00:00+05:30')[0]) == 1
