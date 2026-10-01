"""Actual CLI subprocesses and data files; no feature mocks or external providers."""
import json
import os
import subprocess
import sys

from meemee.context import ContextStore
from meemee.goals import GoalStore
from meemee.monitors import MonitorInput, MonitorStore


def test_production_process_observation_approval_followup_restart(tmp_path):
    root = tmp_path/'feeds'; root.mkdir()
    data = tmp_path/'data'
    env = {key: os.environ[key] for key in ('PATH','HOME','MEEMEE_VAULT_KEY') if key in os.environ}
    env.update(MEEMEE_DATA_DIR=str(data), MEEMEE_PERSISTENCE_BACKEND='sqlite')
    def run(*args):
        return subprocess.run([str(__import__('pathlib').Path(sys.executable).parent/'meemee'),*args],env=env,
                              check=True,capture_output=True,text=True,timeout=30).stdout
    context = ContextStore(data/'context.sqlite3')
    context.register_source('o','feed','local_rss',{'path':'rss.xml'})
    goals = GoalStore(data/'goals.sqlite3')
    goal = goals.create('o','watch',context={'steps':[
        {'kind':'wait','source_id':'feed','contains':'ready'},
        {'kind':'note','text':'Observed real feed'},
        {'kind':'note','text':'Followed up'}]})
    monitor = MonitorStore(data/'monitors.sqlite3').create('o',MonitorInput(
        name='Ready',source_id='feed',field='content',operator='contains',expected='ready'))
    assert 'waiting' in run('agency-worker','o','--once')
    (root/'rss.xml').write_text('<rss><channel><item><guid>1</guid><title>Ready</title><description>ready</description></item></channel></rss>')
    assert json.loads(run('intake-worker',str(root),'--once')) == {'ok':1,'failed':0}
    assert 'observed' in run('agency-worker','o','--once')
    assert 'approval_required' in run('agency-worker','o','--once')
    run('goal-approve','o',goal['id'],'1')
    assert 'acted' in run('agency-worker','o','--once')
    assert 'approval_required' in run('agency-worker','o','--once')
    run('goal-approve','o',goal['id'],'2')
    assert 'completed' in run('agency-worker','o','--once')
    assert 'idle' in run('agency-worker','o','--once')
    assert len(goals.notes('o')) == 2
    monitors = MonitorStore(data/'monitors.sqlite3')
    assert monitors.get('o',monitor['id'])['status'] == 'completed'
    assert len(monitors.notifications('o')) == 1
