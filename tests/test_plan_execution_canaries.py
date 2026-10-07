"""A plan status API must respect dependency and terminal-state boundaries."""
import pytest

from meemee.plan_store import PlanStore
from meemee.types import Plan, PlanStep


@pytest.mark.parametrize('status', ['running', 'done'])
def test_dependent_cannot_run_or_finish_before_dependency(tmp_path, status):
    store = PlanStore(tmp_path / 'plan.db')
    record = store.create(Plan(goal='ship', steps=[PlanStep(id='build', description='build'),
        PlanStep(id='test', description='test', depends_on=['build'])]))
    with pytest.raises(ValueError, match='dependenc'):
        store.update_status(record['id'], 'test', status, 1)
    assert store.get(record['id'])['version'] == 1
    assert len(store.history(record['id'])) == 1


def test_finished_step_cannot_silently_reopen(tmp_path):
    store = PlanStore(tmp_path / 'plan.db')
    record = store.create(Plan(goal='ship', steps=[PlanStep(id='build', description='build', status='done')]))
    with pytest.raises(ValueError, match='transition'):
        store.update_status(record['id'], 'build', 'running', 1)
    assert store.get(record['id'])['version'] == 1


def test_ready_dependency_runs_and_failed_step_can_reset(tmp_path):
    store = PlanStore(tmp_path / 'plan.db')
    record = store.create(Plan(goal='ship', steps=[PlanStep(id='build', description='build'),
        PlanStep(id='test', description='test', depends_on=['build'])]))
    ident = record['id']
    store.update_status(ident, 'build', 'done', 1)
    store.update_status(ident, 'test', 'running', 2)
    store.update_status(ident, 'test', 'failed', 3)
    store.update_status(ident, 'test', 'pending', 4)
    result = store.update_status(ident, 'test', 'done', 5)
    assert result['version'] == 6
    assert store.update_status(ident, 'test', 'done', 6)['version'] == 6
    assert len(store.history(ident)) == 6
