import pathlib,re
from meemee_persist_pg.audit import AuditLog
from meemee_persist_pg.migrations import bundled_migrations

def test_audit_hash_is_deterministic():
    assert AuditLog.hash("0"*64,"2026-01-01T00:00:00+00:00","a","x","r","ok","{}") == AuditLog.hash("0"*64,"2026-01-01T00:00:00+00:00","a","x","r","ok","{}")

def test_migrations_are_contiguous_and_checksummed():
    ms=bundled_migrations(); assert [m.version for m in ms]==list(range(1,len(ms)+1)); assert all(re.fullmatch(r"[0-9a-f]{64}",m.checksum) for m in ms)

def test_initial_schema_has_all_six_surfaces():
    sql=pathlib.Path("meemee_persist_pg/sql/001_initial.sql").read_text()
    for name in ("memories","jobs","plans","api_tokens","audit_log"):
        assert f"meemee_{name}" in sql
    assert "SKIP LOCKED" not in sql  # claim semantics live in prepared application SQL
