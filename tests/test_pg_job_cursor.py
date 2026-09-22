from datetime import datetime, timezone

import pytest

from meemee.cursors import encode_cursor
from meemee_persist_pg.jobs import JobStore


class Result:
    def __init__(self,rows): self.rows=rows
    def fetchall(self): return self.rows
class Connection:
    def __init__(self,rows): self.rows=rows; self.called=None
    def execute(self,sql,args): self.called=(sql,args); return Result(self.rows)
class DB:
    def __init__(self,rows): self.connection=Connection(rows)
    def transaction(self):
        connection=self.connection
        class Context:
            def __enter__(self): return connection
            def __exit__(self,*args): return False
        return Context()


def test_pg_job_list_uses_opaque_keyset_cursor_and_returns_next():
    now=datetime.now(timezone.utc)
    rows=[{"id":f"00000000-0000-0000-0000-00000000000{i}","updated_at":now,"lease_token":None} for i in range(3)]
    store=JobStore(DB(rows)); items,next_cursor=store.list_for_principal("u",limit=2,cursor=encode_cursor(now.isoformat(),rows[0]["id"]))
    assert len(items)==2 and next_cursor
    sql,args=store.db.connection.called
    assert "updated_at<%s OR" in sql and args[0]=="u" and args[-1]==3


def test_pg_job_list_rejects_malformed_cursor_before_query():
    store=JobStore(DB([]))
    with pytest.raises(ValueError): store.list_for_principal("u",cursor="bad")
    assert store.db.connection.called is None
