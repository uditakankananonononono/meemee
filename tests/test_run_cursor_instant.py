"""Run cursors use timestamp instants consistently on both backends."""

from test_token_audit_backends import persistence  # noqa: F401

from meemee.cursors import encode_cursor
from meemee.types import RunReport


def test_run_cursor_normalizes_offset_instant(persistence):  # noqa: F811
    runs = persistence.runs
    runs.add('owner', RunReport(run_id='r1', goal='work', final='done', steps_used=1, tool_results=[]))
    stamp = '2026-10-08T04:00:00+00:00'
    if persistence.backend == 'sqlite':
        with runs.db:
            runs.db.execute('UPDATE runs SET created_at=? WHERE run_id=?', (stamp, 'r1'))
    else:
        with runs.db.transaction() as c:
            c.execute('UPDATE meemee_runs SET created_at=%s WHERE run_id=%s', (stamp, 'r1'))
    assert runs.list('owner', cursor=encode_cursor('2026-10-08T08:00:00+05:30', 'zz'))[0] == []
    # Same instant, id fence must still select smaller id.
    assert len(runs.list('owner', cursor=encode_cursor('2026-10-08T09:30:00+05:30', 'zz'))[0]) == 1
    assert runs.list('owner', cursor=encode_cursor('2026-10-08T09:30:00+05:30', 'aa'))[0] == []
