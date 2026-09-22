import json

import pytest

from meemee.audit import AuditLog
from meemee.auth import TokenStore
from meemee.jobs import JobStore
from meemee.memory import MemoryStore
from meemee.postgres_copy import export_sqlite, import_postgresql, verify_export


def test_deterministic_checked_export_covers_core_stores(tmp_path):
    MemoryStore(tmp_path/"meemee.sqlite3").add("r","fact","safe")
    JobStore(tmp_path/"jobs.sqlite3").enqueue("work",principal="u")
    TokenStore(tmp_path/"auth.sqlite3").create("reader",{"jobs:read"})
    AuditLog(tmp_path/"audit.sqlite3").append("u","x","r","ok")
    target=tmp_path/"copy.json"; report=export_sqlite(tmp_path,target)
    assert report["counts"]=={"memories":1,"jobs":1,"job_events":1,"api_tokens":1,"audit_log":1}
    assert verify_export(target)["format"]=="meemee-sqlite-postgresql-v1"
    doc=json.loads(target.read_text()); doc["tables"]["jobs"][0]["goal"]="forged"; target.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match="checksum"): verify_export(target)


def test_import_refuses_nonempty_and_runs_in_one_serializable_transaction(tmp_path):
    source=tmp_path/"copy.json"; export_sqlite(tmp_path,source)
    class Connection:
        def __init__(self,nonempty=False): self.nonempty=nonempty; self.statements=[]
        def execute(self,sql,args=()):
            self.statements.append((sql,args))
            class Result:
                def __init__(self,value): self.value=value
                def fetchone(self): return self.value
            return Result({"exists":1} if self.nonempty and sql.startswith("SELECT") else None)
    class Tx:
        def __init__(self,connection): self.connection=connection; self.isolation=None
        def transaction(self,isolation=None):
            self.isolation=isolation
            class Context:
                def __enter__(_): return self.connection
                def __exit__(_, *args): return False
            return Context()
    tx=Tx(Connection()); assert import_postgresql(source,tx)["status"]=="imported" and tx.isolation=="SERIALIZABLE"
    with pytest.raises(ValueError,match="not empty"): import_postgresql(source,Tx(Connection(True)))
