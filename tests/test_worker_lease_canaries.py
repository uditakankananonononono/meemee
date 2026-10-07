"""Stateful lease contract exercised through the real worker loop (not live PG)."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from meemee.config import Settings
from meemee.types import RunReport
from meemee.worker import work_forever


class EndLoop(Exception):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize('lost', [False, True])
async def test_worker_renews_lease_and_never_settles_lost_work(tmp_path, monkeypatch, lost):
    from meemee_persist_pg.jobs import LeaseLostError
    import meemee.worker as worker
    class Jobs:
        lease_seconds = 0.15
        claimed = False
        deadline = 0
        beats = 0
        terminals = []
        def claim(self):
            if self.claimed:
                raise EndLoop()
            self.claimed = True
            self.deadline = time.monotonic() + self.lease_seconds
            return {'id':'j', 'goal':'work', 'principal':'owner', 'lease_token':'original'}
        def get(self, ident):
            return {'status':'running', 'lease_token':'original'}
        def heartbeat(self, ident, token):
            self.beats += 1
            if lost or token != 'original' or time.monotonic() >= self.deadline:
                return False
            self.deadline = time.monotonic() + self.lease_seconds
            return True
        def finish(self, ident, result, lease_token=None):
            self.terminals.append(('finish', lease_token))
            if lost or time.monotonic() >= self.deadline:
                raise LeaseLostError('expired')
            return False
        def fail(self, ident, error, lease_token=None):
            self.terminals.append(('fail', lease_token))
            raise LeaseLostError('expired')
        def cancel_running(self, ident, lease_token=None):
            self.terminals.append(('cancel', lease_token))
            return False
    jobs = Jobs()
    events = []
    class Agent:
        async def run(self, goal, **kwargs):
            await asyncio.sleep(0.35)
            return RunReport(run_id='r', goal=goal, final='done', steps_used=1, tool_results=[])
    persistence = SimpleNamespace(jobs=jobs, webhooks=SimpleNamespace(enqueue=lambda *a, **kw: events.append(a)),
        memory=SimpleNamespace(delete_runs=lambda *a, **kw: None), approvals=SimpleNamespace(allows=lambda *a, **kw: False))
    monkeypatch.setattr(worker, 'persistence_from_settings', lambda settings: persistence)
    monkeypatch.setattr(worker, 'build_agent', lambda *a, **kw: Agent())
    with pytest.raises(EndLoop):
        await work_forever(Settings(data_dir=tmp_path))
    assert jobs.beats > 0
    if lost:
        assert jobs.terminals == [] and events == []
    else:
        assert jobs.terminals == [('finish', 'original')]
        assert len(events) == 1 and events[0][1] == 'job.done'


def test_same_lease_error_identity_across_worker_and_pg_backend():
    from meemee.job_errors import LeaseLostError as shared
    from meemee_persist_pg import LeaseLostError as pg
    assert shared is pg
