"""Closed, restartable local goal workflow. No fabricated model intelligence."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .goals import GoalConflict, GoalStore, _iso


class AgencyWorker:
    def __init__(self, goals: GoalStore, context, worker_id: str):
        self.goals, self.context, self.worker_id = goals, context, worker_id
        goals.init_execution()

    def tick(self, principal: str) -> str:
        goal = self.goals.claim(principal, self.worker_id)
        if goal is None:
            return 'idle'
        try:
            steps = goal['context'].get('steps')
            if not isinstance(steps, list) or not 1 <= len(steps) <= 100:
                raise ValueError('goal needs an explicit 1..100 step local plan')
            with self.goals.lock:
                self.goals.db.execute('BEGIN IMMEDIATE')
                try:
                    row = self.goals._owned(principal, goal['id'])
                    if row['status'] != 'active' or row['lease_owner'] != self.worker_id or row['lease_until'] <= _iso():
                        raise GoalConflict('execution lease lost')
                    db = self.goals.db
                    db.execute('INSERT OR IGNORE INTO agency_progress(goal_id) VALUES(?)', (goal['id'],))
                    index = db.execute('SELECT step FROM agency_progress WHERE goal_id=?', (goal['id'],)).fetchone()[0]
                    action = steps[index]
                    state, advance = self._step(principal, goal['id'], index, action)
                    done = advance and index + 1 == len(steps)
                    if advance:
                        db.execute('UPDATE agency_progress SET step=step+1 WHERE goal_id=?', (goal['id'],))
                    status = 'completed' if done else ('pending' if advance else 'blocked')
                    db.execute('UPDATE agency_goals SET status=?,lease_owner=NULL,lease_until=NULL,version=version+1,updated_at=?,outcome=? WHERE id=?',
                               (status, _iso(), 'Local plan completed' if done else None, goal['id']))
                    db.execute('INSERT INTO agency_execution_events(goal_id,principal,step,kind,detail,created_at) VALUES(?,?,?,?,?,?)',
                               (goal['id'], principal, index, state, json.dumps(action, sort_keys=True), _iso()))
                    db.execute('COMMIT')
                    return 'completed' if done else state
                except Exception:
                    db.execute('ROLLBACK')
                    raise
        except GoalConflict:
            return 'lease_lost'
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            self.goals.transition(principal, goal['id'], 'failed', worker_id=self.worker_id, failure=str(exc))
            return 'failed'

    def _step(self, principal, goal_id, index, action):
        if action['kind'] == 'wait':
            max_age = int(action.get('max_age_seconds', 900))
            if not 1 <= max_age <= 86400:
                raise ValueError('freshness must be 1..86400 seconds')
            clock = datetime.now(timezone.utc)
            for record in self.context.recent(principal, limit=100):
                stamp = datetime.fromisoformat(record['occurred_at'].replace('Z', '+00:00'))
                if stamp.tzinfo is None:
                    continue
                age = (clock - stamp).total_seconds()
                if record['source_id'] == action['source_id'] and 0 <= age <= max_age and action['contains'] in record['content'] and record['provenance']:
                    return 'observed', True
            return 'waiting', False
        if action['kind'] == 'note':
            text = action['text']
            if not isinstance(text, str) or not 1 <= len(text) <= 20_000:
                raise ValueError('note text must be 1..20000 characters')
            digest = hashlib.sha256(json.dumps(action, sort_keys=True).encode()).hexdigest()
            grant = self.goals.db.execute('SELECT digest FROM agency_step_grants WHERE goal_id=? AND step=? AND principal=?',
                                         (goal_id, index, principal)).fetchone()
            if not grant or grant[0] != digest:
                return 'approval_required', False
            # The real local effect and progress acknowledgement are one transaction.
            self.goals.db.execute('INSERT OR IGNORE INTO agency_notes VALUES(?,?,?,?,?)',
                                  (goal_id, index, principal, text, _iso()))
            return 'acted', True
        raise ValueError('unsupported local step kind; no fallback action')
