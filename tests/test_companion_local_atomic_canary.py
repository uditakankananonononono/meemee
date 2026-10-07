"""Local check-in message and completion share a commit, including process death."""
import os
import subprocess
import sys

import pytest
from test_companion_worker import queue_checkin, setup

from meemee.companion.store import CompanionStore
from meemee.companion.worker import deliver_due_once


@pytest.mark.asyncio
async def test_completion_abort_cannot_publish_local_message(tmp_path):
    store, engine, channels = setup(tmp_path)
    queue_checkin(store)
    store.db.execute("""CREATE TRIGGER reject_done BEFORE UPDATE OF status ON companion_checkins
                      WHEN NEW.status='done' BEGIN SELECT RAISE(ABORT,'completion rejected'); END""")
    with pytest.raises(Exception, match='completion rejected'):
        await deliver_due_once(store, engine, channels)
    conv = store.latest_conversation('udita', 'local')
    assert store.history(conv['id']) == []


def test_process_death_at_completion_rolls_back_local_message(tmp_path):
    script = '''
import asyncio, os, sys
from datetime import datetime, timezone
from pathlib import Path
from meemee.companion.channels import LocalChannel
from meemee.companion.models import UserProfile, CheckInPreferences
from meemee.companion.store import CompanionStore
from meemee.companion.worker import deliver_due_once
store = CompanionStore(Path(sys.argv[1]))
store.upsert_user(UserProfile(user_id='owner',display_name='Owner',checkins=CheckInPreferences(enabled=True)))
store.schedule_checkin('owner',datetime.now(timezone.utc),'slot','local',None)
store.db.create_function('crash',0,lambda: os._exit(73))
store.db.execute("CREATE TRIGGER crash_done BEFORE UPDATE OF status ON companion_checkins WHEN NEW.status='done' BEGIN SELECT crash(); END")
class Engine:
    async def checkin_message(self, user): return 'atomic local message'
asyncio.run(deliver_due_once(store,Engine(),{'local':LocalChannel(store)}))
'''
    path = tmp_path / 'c.db'
    proc = subprocess.run([sys.executable, '-c', script, str(path)], env=os.environ.copy(), check=False)
    assert proc.returncode == 73
    store = CompanionStore(path)
    conv = store.latest_conversation('owner', 'local')
    assert store.history(conv['id']) == []
    assert store.list_checkins('owner')[0]['status'] == 'running'
