"""Run with MEEMEE_TEST_POSTGRES_DSN pointed at an expendable empty database."""
import os
import pytest

pytestmark=pytest.mark.skipif(not os.getenv("MEEMEE_TEST_POSTGRES_DSN"),reason="requires real PostgreSQL")

def db():
    from meemee_persist_pg import Database,MigrationStore
    value=Database(os.environ["MEEMEE_TEST_POSTGRES_DSN"],min_size=1,max_size=4)
    MigrationStore(value).apply(); return value

def test_memory_tokens_audit_and_migration_idempotency():
    from meemee_persist_pg import MemoryStore,TokenStore,AuditLog,MigrationStore
    value=db()
    try:
        memory=MemoryStore(value); ident=memory.add("run-i","fact","alpha beta",{"safe":True})
        assert memory.search("alpha")[0]["id"]==ident
        tokens=TokenStore(value); token_id,raw=tokens.create("integration",{"jobs:read"})
        assert tokens.authenticate(raw).id==token_id; assert tokens.revoke(token_id); assert tokens.authenticate(raw) is None
        audit=AuditLog(value); audit.append("test","write",str(ident),"success",{"x":1}); assert audit.verify()==(True,None)
        assert MigrationStore(value).apply()==[]
    finally:value.close()

def test_skip_locked_claim_and_fencing():
    from meemee_persist_pg import JobStore,LeaseLostError
    value=db()
    try:
        first,second=JobStore(value,worker_id="one"),JobStore(value,worker_id="two")
        ident=first.enqueue("exclusive integration job")
        claimed=first.claim(); assert claimed["id"]==ident and second.claim() is None
        with pytest.raises(LeaseLostError): second.finish(ident,{"bad":True},claimed["lease_token"])
        first.finish(ident,{"ok":True},claimed["lease_token"]); assert first.get(ident)["status"]=="done"
    finally:value.close()
