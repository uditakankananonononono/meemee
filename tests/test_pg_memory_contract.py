from meemee_persist_pg.memory import MemoryStore


class Result:
    def __init__(self,row=None,rows=None): self.row=row; self.rows=rows or []
    def fetchone(self): return self.row
    def fetchall(self): return self.rows
class Connection:
    def __init__(self): self.last=None
    def execute(self,sql,args):
        self.last=(sql,args)
        if sql.startswith("INSERT"): return Result({"id":1})
        return Result(rows=[{"id":1,"content":"database restore","metadata":{},"score":1.0}])
class DB:
    def __init__(self): self.connection=Connection()
    def transaction(self):
        connection=self.connection
        class Context:
            def __enter__(self): return connection
            def __exit__(self,*args): return False
        return Context()

def test_pg_memory_matches_agent_hybrid_contract_and_scrubs_secrets():
    db=DB(); store=MemoryStore(db)
    store.add("r","fact","token ghp_abcdefghijklmnopqrstuvwxyz123456")
    assert "ghp_" not in db.connection.last[1][2]
    assert store.hybrid_search("database restore",5)[0]["id"]==1
    assert store.semantic_search("database restore",5)[0]["score"]==1.0
