from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

MonitorStatus=Literal['active','triggered','completed','timed_out','cancelled']
class MonitorInput(BaseModel):
    name:str=Field(min_length=1,max_length=240)
    source_id:str=Field(min_length=1,max_length=240)
    field:str=Field(min_length=1,max_length=120)
    operator:Literal['eq','contains','gt','gte','lt','lte','exists']
    expected:str|float|bool|None=None
    deadline:str|None=None
    max_fires:int=1

class MonitorStore:
    def __init__(self,path:Path):
        path.parent.mkdir(parents=True,exist_ok=True);self.db=sqlite3.connect(path,check_same_thread=False);self.db.row_factory=sqlite3.Row;self.lock=threading.RLock()
        with self.lock,self.db:self.db.executescript('''PRAGMA journal_mode=WAL; CREATE TABLE IF NOT EXISTS monitors(id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,name TEXT NOT NULL,source_id TEXT NOT NULL,predicate TEXT NOT NULL,deadline TEXT,max_fires INTEGER NOT NULL,fire_count INTEGER NOT NULL DEFAULT 0,status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL); CREATE INDEX IF NOT EXISTS monitors_owner_status ON monitors(owner_id,status,deadline); CREATE TABLE IF NOT EXISTS monitor_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,monitor_id TEXT NOT NULL,owner_id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL); CREATE INDEX IF NOT EXISTS monitor_events_item ON monitor_events(owner_id,monitor_id,sequence);''')
    def create(self,owner_id:str,item:MonitorInput)->dict:
        if not owner_id:raise ValueError('owner is required')
        ident=uuid.uuid4().hex;now=datetime.now(timezone.utc).isoformat();predicate={'field':item.field,'operator':item.operator,'expected':item.expected}
        with self.lock,self.db:self.db.execute('INSERT INTO monitors VALUES(?,?,?,?,?,?,?,0,?,?,?)',(ident,owner_id,item.name,item.source_id,json.dumps(predicate,sort_keys=True),item.deadline,item.max_fires,'active',now,now));self._event(ident,owner_id,'created',{'predicate':predicate})
        return self.get(owner_id,ident) or {}
    def _event(self,ident,owner,kind,payload):self.db.execute('INSERT INTO monitor_events(monitor_id,owner_id,kind,payload,created_at) VALUES(?,?,?,?,?)',(ident,owner,kind,json.dumps(payload,sort_keys=True),datetime.now(timezone.utc).isoformat()))
    def get(self,owner_id,ident):
        with self.lock:row=self.db.execute('SELECT * FROM monitors WHERE owner_id=? AND id=?',(owner_id,ident)).fetchone()
        if not row:return None
        result=dict(row);result['predicate']=json.loads(result['predicate']);return result
    def list(self,owner_id,status=None):
        query='SELECT id FROM monitors WHERE owner_id=?';values=[owner_id]
        if status:query+=' AND status=?';values.append(status)
        query+=' ORDER BY created_at DESC,id DESC'
        with self.lock:rows=self.db.execute(query,values).fetchall()
        return [self.get(owner_id,row['id']) for row in rows]
    @staticmethod
    def matches(predicate,event):
        value=event.get(predicate['field']);op=predicate['operator'];expected=predicate.get('expected')
        if op=='exists':return value is not None
        if op=='eq':return value==expected
        if op=='contains':return str(expected).lower() in str(value or '').lower()
        try:
            return {'gt':value>expected,'gte':value>=expected,'lt':value<expected,'lte':value<=expected}[op]
        except (TypeError,KeyError):return False
    def evaluate(self,owner_id,source_id,event,at=None):
        clock=at or datetime.now(timezone.utc).isoformat();fired=[]
        with self.lock,self.db:
            rows=self.db.execute("SELECT * FROM monitors WHERE owner_id=? AND source_id=? AND status='active'",(owner_id,source_id)).fetchall()
            for row in rows:
                if row['deadline'] and row['deadline']<=clock:self.db.execute("UPDATE monitors SET status='timed_out',updated_at=? WHERE id=?",(clock,row['id']));self._event(row['id'],owner_id,'timed_out',{});continue
                predicate=json.loads(row['predicate'])
                if self.matches(predicate,event):
                    count=row['fire_count']+1;status='completed' if count>=row['max_fires'] else 'active';self.db.execute('UPDATE monitors SET fire_count=?,status=?,updated_at=? WHERE id=?',(count,status,clock,row['id']));self._event(row['id'],owner_id,'triggered',event);fired.append(row['id'])
        return fired
    def cancel(self,owner_id,ident):
        now=datetime.now(timezone.utc).isoformat()
        with self.lock,self.db:
            changed=self.db.execute("UPDATE monitors SET status='cancelled',updated_at=? WHERE owner_id=? AND id=? AND status='active'",(now,owner_id,ident)).rowcount
            if changed:self._event(ident,owner_id,'cancelled',{})
        return bool(changed)
    def events(self,owner_id,ident):
        with self.lock:rows=self.db.execute('SELECT sequence,kind,payload,created_at FROM monitor_events WHERE owner_id=? AND monitor_id=? ORDER BY sequence',(owner_id,ident)).fetchall()
        return [{**dict(row),'payload':json.loads(row['payload'])} for row in rows]

    def delete_owner(self, owner_id: str) -> dict[str, int]:
        """Hard-delete every monitor and monitor event owned by an account."""
        if not owner_id: raise ValueError('owner is required')
        with self.lock, self.db:
            events = self.db.execute('DELETE FROM monitor_events WHERE owner_id=?', (owner_id,)).rowcount
            monitors = self.db.execute('DELETE FROM monitors WHERE owner_id=?', (owner_id,)).rowcount
        return {"monitors": monitors, "monitor_events": events}
