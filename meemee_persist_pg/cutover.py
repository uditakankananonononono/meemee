from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from ._db import Database


@dataclass(frozen=True,slots=True)
class TableSpec:
    source:str; target:str; columns:tuple[str,...]; order:tuple[str,...]

SPECS={
 "memory":(TableSpec("memories","meemee_memories",("id","run_id","kind","content","metadata","created_at"),("id",)),),
 "plans":(TableSpec("plans","meemee_plans",("id","goal","version","document","created_at","updated_at"),("id",)),TableSpec("plan_history","meemee_plan_history",("plan_id","version","document","reason","created_at"),("plan_id","version"))),
 "jobs":(TableSpec("jobs","meemee_jobs",("id","goal","run_at","status","attempts","max_attempts","result","error","created_at","updated_at"),("id",)),TableSpec("job_events","meemee_job_events",("sequence","job_id","kind","payload","created_at"),("sequence",))),
 "tokens":(TableSpec("api_tokens","meemee_api_tokens",("id","name","digest","scopes","created_at","last_used_at","expires_at","revoked_at","owner_id","token_kind"),("id",)),
           TableSpec("accounts","meemee_accounts",("id","email","display_name","password_hash","password_salt","created_at","disabled_at","failed_logins","locked_until"),("id",))),
 "audit":(TableSpec("audit_log","meemee_audit_log",("sequence","occurred_at","actor_id","action","resource","outcome","metadata","previous_hash","entry_hash"),("sequence",)),
          TableSpec("audit_chain_base","meemee_audit_chain_base",("singleton","sequence","entry_hash"),("singleton",))),
 "email":(TableSpec("email_verifications","meemee_email_verifications",("account_id","token_digest","expires_at","verified_at","sent_at"),("account_id",)),
          TableSpec("password_resets","meemee_password_resets",("account_id","token_digest","expires_at","used_at","sent_at"),("account_id",))),
 "quotas":(TableSpec("quota_limits","meemee_quota_limits",("principal","daily_jobs"),("principal",)),
           TableSpec("quota_usage","meemee_quota_usage",("principal","day","jobs"),("principal","day"))),
 "entitlements":(TableSpec("principal_plans","meemee_principal_plans",("principal","plan","updated_at"),("principal",)),),
 "approvals":(TableSpec("tool_approvals","meemee_tool_approvals",("principal","tool","granted_at","expires_at","revoked_at","granted_by","argument_constraints"),("principal","tool")),),
}
# Groups an operator may leave out of a cutover. approvals.sqlite3 only exists once a grant was made,
# and older cutover invocations predate it; when given, it is copied and verified like every other group.
OPTIONAL_GROUPS=frozenset({"approvals","email","quotas","entitlements"})
JSON_COLUMNS={"metadata","document","result","payload","argument_constraints"}
UUID_COLUMNS={"id","plan_id","job_id"}
_DATE_ONLY=re.compile(r"\d{4}-\d{2}-\d{2}")
IDENTITY_TARGETS={"meemee_memories","meemee_job_events","meemee_audit_log"}

@contextmanager
def sqlite_snapshot(path:Path)->Iterator[sqlite3.Connection]:
    resolved=path.resolve(strict=True)
    conn=sqlite3.connect(f"file:{resolved}?mode=ro",uri=True,isolation_level=None)
    conn.row_factory=sqlite3.Row
    try:
        conn.execute("PRAGMA query_only=ON"); conn.execute("BEGIN")
        yield conn
        conn.execute("ROLLBACK")
    finally:conn.close()

def _json(value):
    if value is None:return None
    if isinstance(value,(dict,list)):return value
    return json.loads(value)

def transform(spec:TableSpec,row:sqlite3.Row)->tuple[Any,...]:
    values=[]
    for col in spec.columns:
        value=row[col]
        if col in JSON_COLUMNS:value=_json(value)
        elif spec.target=="meemee_api_tokens" and col=="scopes":value=str(value).split()
        elif col in UUID_COLUMNS and spec.target in {"meemee_plans","meemee_plan_history","meemee_jobs","meemee_job_events"}:value=uuid.UUID(str(value))
        values.append(value)
    return tuple(values)

def insert_values(spec:TableSpec,row:sqlite3.Row)->tuple[Any,...]:
    """transform() output adapted for INSERT: decoded JSON columns go in as jsonb (psycopg will not adapt a bare dict)."""
    return tuple(Jsonb(value) if col in JSON_COLUMNS and value is not None else value for col,value in zip(spec.columns,transform(spec,row)))

def canonical(value:Any)->Any:
    if isinstance(value,memoryview):return bytes(value).hex()
    if isinstance(value,bytes):return value.hex()
    if isinstance(value,uuid.UUID):return str(value)
    if isinstance(value,datetime):return value.astimezone(timezone.utc).isoformat(timespec="microseconds")
    if isinstance(value,date):return value.isoformat()  # calendar dates (quota days) carry no zone
    if isinstance(value,str):
        if _DATE_ONLY.fullmatch(value):return value
        try:return datetime.fromisoformat(value).astimezone(timezone.utc).isoformat(timespec="microseconds")
        except (ValueError,TypeError):return value
    if isinstance(value,dict):return {k:canonical(value[k]) for k in sorted(value)}
    if isinstance(value,(list,tuple)):return [canonical(v) for v in value]
    return value

def digest_rows(rows)->tuple[int,str]:
    digest=hashlib.sha256(); count=0
    for row in rows:
        encoded=json.dumps(canonical(dict(row) if hasattr(row,"keys") else row),sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
        digest.update(len(encoded).to_bytes(8,"big"));digest.update(encoded);count+=1
    return count,digest.hexdigest()

class Cutover:
    """Offline, repeatable SQLite-to-PostgreSQL copy and exact canonical verification."""
    def __init__(self,db:Database,sources:dict[str,Path]):
        missing=set(SPECS)-OPTIONAL_GROUPS-set(sources)
        if missing:raise ValueError(f"missing SQLite sources: {sorted(missing)}")
        if unknown:=set(sources)-set(SPECS):raise ValueError(f"unknown SQLite sources: {sorted(unknown)}")
        self.db,self.sources=db,{k:Path(v) for k,v in sources.items()}
        self.specs={group:specs for group,specs in SPECS.items() if group in self.sources}

    def copy(self,*,batch_size:int=1000)->dict[str,int]:
        if batch_size<1:raise ValueError("batch_size must be positive")
        copied={}
        # Each SQLite file is held in one read transaction; PostgreSQL commits only if all stores copy.
        with ExitStack() as stack:
            src={name:stack.enter_context(sqlite_snapshot(path)) for name,path in self.sources.items()}
            versions={name:conn.execute("PRAGMA data_version").fetchone()[0] for name,conn in src.items()}
            with self.db.transaction(isolation="SERIALIZABLE") as target:
                for group,specs in self.specs.items():
                    for spec in specs:
                        existing=target.execute(f"SELECT count(*) AS n FROM {spec.target}").fetchone()["n"]
                        if existing:raise RuntimeError(f"target {spec.target} is not empty ({existing} rows); refusing a mixed cutover")
                        columns=",".join(spec.columns); placeholders=",".join(["%s"]*len(spec.columns))
                        override=" OVERRIDING SYSTEM VALUE" if spec.target in IDENTITY_TARGETS else ""
                        statement=f"INSERT INTO {spec.target} ({columns}){override} VALUES ({placeholders})"
                        cursor=src[group].execute(f"SELECT {columns} FROM {spec.source} ORDER BY {','.join(spec.order)}")
                        count=0
                        while True:
                            rows=cursor.fetchmany(batch_size)
                            if not rows:break
                            # psycopg 3 exposes executemany on cursors only; the Connection shortcut does not exist.
                            with target.cursor() as batch:batch.executemany(statement,[insert_values(spec,row) for row in rows])
                            count+=len(rows)
                        copied[spec.target]=count
                changed=[name for name,conn in src.items() if conn.execute("PRAGMA data_version").fetchone()[0] != versions[name]]
                if changed: raise RuntimeError(f"SQLite writers were active during copy: {changed}; PostgreSQL copy rolled back")
                for table in IDENTITY_TARGETS:
                    column="id" if table=="meemee_memories" else "sequence"
                    target.execute("SELECT setval(pg_get_serial_sequence(%s,%s),COALESCE((SELECT max("+column+") FROM "+table+"),1),EXISTS(SELECT 1 FROM "+table+"))",(table,column))
        return copied

    def verify(self)->dict[str,dict[str,Any]]:
        report={}
        with ExitStack() as stack:
            src={name:stack.enter_context(sqlite_snapshot(path)) for name,path in self.sources.items()}
            with self.db.transaction(isolation="REPEATABLE READ") as target:
                for group,specs in self.specs.items():
                    for spec in specs:
                        columns=",".join(spec.columns); order=",".join(spec.order)
                        source_rows=(dict(zip(spec.columns,transform(spec,row))) for row in src[group].execute(f"SELECT {columns} FROM {spec.source} ORDER BY {order}"))
                        source_count,source_hash=digest_rows(source_rows)
                        pg_rows=target.execute(f"SELECT {columns} FROM {spec.target} ORDER BY {order}")
                        target_count,target_hash=digest_rows(pg_rows)
                        report[spec.target]={"source_count":source_count,"target_count":target_count,"source_sha256":source_hash,"target_sha256":target_hash,"match":source_count==target_count and source_hash==target_hash}
        return report

    def dual_verify(self,*,cycles:int,interval_seconds:float)->list[dict[str,dict[str,Any]]]:
        if cycles<1 or interval_seconds<0:raise ValueError("cycles must be positive and interval non-negative")
        results=[]
        for cycle in range(cycles):
            results.append(self.verify())
            if cycle+1<cycles:time.sleep(interval_seconds)
        return results
