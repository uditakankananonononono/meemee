from meemee_persist_pg.rate_limit import PostgreSQLRateLimiter


class Result:
    rowcount=2
    def __init__(self,count=None): self.count=count
    def fetchone(self): return {"count":self.count}
class Connection:
    def __init__(self): self.counts={}; self.calls=[]
    def execute(self,sql,args=()):
        self.calls.append((sql,args))
        if sql.startswith("INSERT"):
            self.counts[args]=self.counts.get(args,0)+1; return Result(self.counts[args])
        return Result()
class DB:
    def __init__(self): self.connection=Connection()
    def transaction(self):
        connection=self.connection
        class Context:
            def __enter__(self): return connection
            def __exit__(self,*args): return False
        return Context()

def test_pg_limiter_is_atomic_shaped_and_cross_instance_shared():
    db=DB(); first=PostgreSQLRateLimiter(db,2,60); second=PostgreSQLRateLimiter(db,2,60)
    assert first.hit("u",120)==(True,1,180)
    assert second.hit("u",120)==(True,0,180)
    assert first.hit("u",120)==(False,0,180)
    assert "ON CONFLICT" in db.connection.calls[-1][0]

def test_pg_limiter_cleanup_reports_deleted_count():
    limiter=PostgreSQLRateLimiter(DB(),2,60)
    assert limiter.cleanup(180)==2
