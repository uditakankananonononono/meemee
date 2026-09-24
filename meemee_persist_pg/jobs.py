from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from ._db import Database


class LeaseLostError(RuntimeError): pass
class JobStore:
    def __init__(self,db:Database,*,worker_id:str|None=None,lease_seconds:int=60):
        self.db,self.worker_id,self.lease_seconds=db,worker_id or uuid.uuid4().hex,lease_seconds
    @staticmethod
    def _row(row):
        if not row:return None
        result=dict(row); result["id"]=str(result["id"]); result["lease_token"]=str(result["lease_token"]) if result.get("lease_token") else None
        return result
    def _event(self,c,ident,kind,payload): c.execute("INSERT INTO meemee_job_events(job_id,kind,payload) VALUES(%s,%s,%s)",(ident,kind,Jsonb(payload)))
    def enqueue(self,goal:str,run_at:datetime|None=None,max_attempts:int=3,principal:str|None=None)->str:
        if not goal.strip() or max_attempts<1: raise ValueError("goal and positive max_attempts required")
        ident=uuid.uuid4()
        with self.db.transaction() as c:
            c.execute("INSERT INTO meemee_jobs(id,goal,run_at,status,max_attempts,principal) VALUES(%s,%s,COALESCE(%s,clock_timestamp()),'queued',%s,%s)",(ident,goal,run_at,max_attempts,principal)); self._event(c,ident,"queued",{"run_at":run_at.isoformat() if run_at else None})
        return str(ident)
    def _reap(self,c):
        rows=c.execute("""UPDATE meemee_jobs SET status=CASE WHEN attempts<max_attempts THEN 'queued'::meemee_job_status ELSE 'failed'::meemee_job_status END,
          error='worker lease expired',lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=clock_timestamp()
          WHERE status='running' AND lease_expires_at<=clock_timestamp() RETURNING id,status""").fetchall()
        for r in rows:self._event(c,r["id"],"retry" if r["status"]=="queued" else "failed",{"error":"worker lease expired"})
        # A cancel requested while the worker died would otherwise sit in cancel_requested forever.
        for r in c.execute("""UPDATE meemee_jobs SET status='cancelled',error='cancelled; worker lease expired',lease_owner=NULL,
          lease_token=NULL,lease_expires_at=NULL,updated_at=clock_timestamp()
          WHERE status='cancel_requested' AND lease_expires_at<=clock_timestamp() RETURNING id""").fetchall():
            self._event(c,r["id"],"cancelled",{"error":"worker lease expired after cancel request"})
    def claim(self)->dict[str,Any]|None:
        token=uuid.uuid4()
        with self.db.transaction() as c:
            self._reap(c)
            row=c.execute("""WITH candidate AS (SELECT id FROM meemee_jobs WHERE status='queued' AND run_at<=clock_timestamp()
              ORDER BY run_at,id FOR UPDATE SKIP LOCKED LIMIT 1) UPDATE meemee_jobs j SET status='running',attempts=attempts+1,
              lease_owner=%s,lease_token=%s,lease_expires_at=clock_timestamp()+(%s*interval '1 second'),updated_at=clock_timestamp()
              FROM candidate WHERE j.id=candidate.id RETURNING j.*""",(self.worker_id,token,self.lease_seconds)).fetchone()
            if row:self._event(c,row["id"],"running",{"attempt":row["attempts"],"worker":self.worker_id})
        return self._row(row)
    def heartbeat(self,ident:str,lease_token:str)->bool:
        with self.db.transaction() as c:return bool(c.execute("""UPDATE meemee_jobs SET lease_expires_at=clock_timestamp()+(%s*interval '1 second'),updated_at=clock_timestamp()
          WHERE id=%s AND status IN ('running','cancel_requested') AND lease_owner=%s AND lease_token=%s AND lease_expires_at>clock_timestamp()""",(self.lease_seconds,ident,self.worker_id,lease_token)).rowcount)
    def _terminal(self,ident,lease_token,status,result=None,error=None):
        with self.db.transaction() as c:
            row=c.execute("""UPDATE meemee_jobs SET status=%s,result=%s,error=%s,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=clock_timestamp()
             WHERE id=%s AND status IN ('running','cancel_requested') AND lease_owner=%s AND lease_token=%s AND lease_expires_at>clock_timestamp() RETURNING id""",(status,Jsonb(result) if result is not None else None,error,ident,self.worker_id,lease_token)).fetchone()
            if not row: raise LeaseLostError(f"job {ident} lease is no longer owned")
            self._event(c,ident,status,{"result":result} if result is not None else {"error":error})
    def finish(self,ident:str,result:dict[str,Any],lease_token:str|None=None)->None:
        token=lease_token or self._owned_token(ident); self._terminal(ident,token,"done",result=result)
    def fail(self,ident:str,error:str,lease_token:str|None=None)->None:
        token=lease_token or self._owned_token(ident)
        with self.db.transaction() as c:
            row=c.execute("""UPDATE meemee_jobs SET status=CASE WHEN attempts<max_attempts THEN 'queued'::meemee_job_status ELSE 'failed'::meemee_job_status END,
             error=%s,lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,updated_at=clock_timestamp() WHERE id=%s AND status='running' AND lease_owner=%s AND lease_token=%s
             AND lease_expires_at>clock_timestamp() RETURNING status""",(error,ident,self.worker_id,token)).fetchone()
            if not row:raise LeaseLostError(f"job {ident} lease is no longer owned")
            self._event(c,ident,"retry" if row["status"]=="queued" else "failed",{"error":error})
    def _owned_token(self,ident):
        with self.db.transaction() as c:r=c.execute("SELECT lease_token FROM meemee_jobs WHERE id=%s AND lease_owner=%s",(ident,self.worker_id)).fetchone()
        if not r:raise LeaseLostError(f"job {ident} lease is no longer owned")
        return str(r["lease_token"])
    def request_cancel(self,ident:str)->str|None:
        with self.db.transaction() as c:
            r=c.execute("""UPDATE meemee_jobs SET status=CASE status WHEN 'queued' THEN 'cancelled'::meemee_job_status WHEN 'running' THEN 'cancel_requested'::meemee_job_status ELSE status END,
             updated_at=clock_timestamp() WHERE id=%s RETURNING status""",(ident,)).fetchone()
            if r:self._event(c,ident,str(r["status"]),{})
            return str(r["status"]) if r else None
    def cancel_running(self,ident:str,lease_token:str|None=None)->bool:
        try:self._terminal(ident,lease_token or self._owned_token(ident),"cancelled",error="cancelled");return True
        except LeaseLostError:return False
    def cancel(self,ident:str)->bool:return self.request_cancel(ident)=="cancelled"
    def get(self,ident:str):
        with self.db.transaction() as c:return self._row(c.execute("SELECT * FROM meemee_jobs WHERE id=%s",(ident,)).fetchone())
    def events(self,ident:str,after:int=0):
        with self.db.transaction() as c:return list(c.execute("SELECT * FROM meemee_job_events WHERE job_id=%s AND sequence>%s ORDER BY sequence",(ident,after)).fetchall())

    def list_for_principal(self,principal:str,status:str|None=None,before:str|None=None,limit:int=100,cursor:str|None=None):
        from meemee.cursors import decode_cursor, encode_cursor
        clauses=["principal=%s"]; params:list[Any]=[principal]
        if status is not None: clauses.append("status=%s"); params.append(status)
        if before is not None: clauses.append("updated_at<%s"); params.append(before)
        if cursor is not None:
            cursor_time,cursor_id=decode_cursor(cursor)
            clauses.append("(updated_at<%s OR (updated_at=%s AND id<%s))")
            params.extend((cursor_time,cursor_time,cursor_id))
        page_size=max(1,min(limit,500)); params.append(page_size+1)
        with self.db.transaction() as c: rows=c.execute(f"SELECT * FROM meemee_jobs WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC,id DESC LIMIT %s",tuple(params)).fetchall()
        items=[self._row(row) for row in rows[:page_size]]
        next_cursor=encode_cursor(str(items[-1]["updated_at"]),items[-1]["id"]) if len(rows)>page_size else None
        return items,next_cursor
    def get_owned(self,ident:str,principal:str):
        with self.db.transaction() as c:return self._row(c.execute("SELECT * FROM meemee_jobs WHERE id=%s AND principal=%s",(ident,principal)).fetchone())
