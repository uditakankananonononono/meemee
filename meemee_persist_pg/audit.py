from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from psycopg.types.json import Jsonb

from ._db import Database

_LOCK=6758712042962292

class AuditLog:
    def __init__(self,db:Database):self.db=db
    @staticmethod
    def hash(previous,occurred,actor,action,resource,outcome,metadata):
        canonical=f"{previous}\x1f{occurred}\x1f{actor}\x1f{action}\x1f{resource}\x1f{outcome}\x1f{metadata}"
        return hashlib.sha256(canonical.encode()).hexdigest()
    def append(self,actor:str,action:str,resource:str,outcome:str,metadata:dict[str,Any]|None=None)->int:
        occurred=datetime.now(timezone.utc); encoded=json.dumps(metadata or {},sort_keys=True,separators=(",",":")); previous="0"*64
        with self.db.transaction(isolation="SERIALIZABLE") as c:
            c.execute("SELECT pg_advisory_xact_lock(%s)",(_LOCK,))
            row=c.execute("SELECT entry_hash FROM meemee_audit_log ORDER BY sequence DESC LIMIT 1").fetchone()
            if row:previous=row["entry_hash"]
            digest=self.hash(previous,occurred.isoformat(),actor,action,resource,outcome,encoded)
            result=c.execute("""INSERT INTO meemee_audit_log(occurred_at,actor_id,action,resource,outcome,metadata,previous_hash,entry_hash)
             VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING sequence""",(occurred,actor,action,resource,outcome,Jsonb(metadata or {}),previous,digest)).fetchone()
            return int(result["sequence"])
    def verify(self)->tuple[bool,int|None]:
        previous="0"*64
        with self.db.transaction(isolation="REPEATABLE READ") as c:rows=c.execute("SELECT * FROM meemee_audit_log ORDER BY sequence").fetchall()
        for row in rows:
            encoded=json.dumps(row["metadata"],sort_keys=True,separators=(",",":")); occurred=row["occurred_at"].astimezone(timezone.utc).isoformat()
            digest=self.hash(previous,occurred,row["actor_id"],row["action"],row["resource"],row["outcome"],encoded)
            if row["previous_hash"]!=previous or row["entry_hash"]!=digest:return False,row["sequence"]
            previous=row["entry_hash"]
        return True,None
    def list(self,after:int=0,limit:int=100):
        with self.db.transaction() as c:return list(c.execute("SELECT * FROM meemee_audit_log WHERE sequence>%s ORDER BY sequence LIMIT %s",(after,max(1,min(limit,500)))).fetchall())
