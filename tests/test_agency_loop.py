import pytest

from meemee.agency import AgencyWorker
from meemee.context import ContextRecord, ContextStore
from meemee.goals import GoalStore


def test_observe_approve_act_followup_restart(tmp_path):
    path = tmp_path / 'goals.db'
    goals = GoalStore(path)
    context = ContextStore(tmp_path / 'context.db')
    context.register_source('o', 'feed', 'rss', {})
    goal = goals.create('o', 'Watch and record', context={'steps': [
        {'kind': 'wait', 'source_id': 'feed', 'contains': 'ready', 'max_age_seconds': 600},
        {'kind': 'note', 'text': 'Ready recorded'},
        {'kind': 'note', 'text': 'Follow-up complete'}]})
    worker = AgencyWorker(goals, context, 'w')
    assert worker.tick('o') == 'waiting'
    context.ingest(ContextRecord('o','feed','1','document','Ready','ready',
                                '2099-01-01T00:00:00+00:00', {'source': 'fixture'}))
    # Future-dated evidence is not fresh evidence.
    goals.wake('o', goal['id'])
    assert worker.tick('o') == 'waiting'
    from datetime import datetime, timezone
    context.ingest(ContextRecord('o','feed','2','document','Ready','ready',
                                datetime.now(timezone.utc).isoformat(), {'source': 'local'}))
    goals.wake('o', goal['id'])
    assert worker.tick('o') == 'observed'
    assert worker.tick('o') == 'approval_required'
    assert goals.notes('o') == []
    goals.approve_step('o', goal['id'], 1)
    assert AgencyWorker(GoalStore(path), context, 'restarted').tick('o') == 'acted'
    assert worker.tick('o') == 'approval_required'
    goals.approve_step('o', goal['id'], 2)
    assert worker.tick('o') == 'completed'
    assert len(goals.notes('o')) == 2
    assert worker.tick('o') == 'idle'
    assert goals.notes('other') == []


def test_competition_dependencies_revocation_cancel_and_atomic_recovery(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    path = tmp_path / 'g.db'
    goals = GoalStore(path)
    context = ContextStore(tmp_path / 'c.db')
    goal = goals.create('o', 'record', context={'steps': [{'kind':'note','text':'once'}]})
    goals.approve_step('o', goal['id'], 0)
    workers = [AgencyWorker(GoalStore(path), context, str(i)) for i in range(2)]
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda worker: worker.tick('o'), workers))
    assert len(goals.notes('o')) == 1
    dependent = goals.create('o','dependent',depends_on=[goal['id']],
                            context={'steps':[{'kind':'note','text':'second'}]})
    goals.approve_step('o', dependent['id'], 0)
    goals.revoke_step('o', dependent['id'], 0)
    assert workers[0].tick('o') == 'approval_required'
    goals.transition('o', dependent['id'], 'cancelled')
    assert workers[1].tick('o') == 'idle'
    assert len(goals.notes('o')) == 1
    with pytest.raises(KeyError): goals.approve_step('other', goal['id'], 0)
