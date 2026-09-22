import pytest

from meemee.persistence import build_persistence


def test_sqlite_composition_selects_core_stores(tmp_path):
    selected=build_persistence("sqlite",tmp_path)
    assert selected.backend=="sqlite"
    assert selected.memory.add("r","fact","works") > 0
    assert selected.jobs.enqueue("work")


def test_postgres_selection_fails_closed_without_dsn(tmp_path):
    with pytest.raises(ValueError,match="MEEMEE_POSTGRES_DSN"):
        build_persistence("postgresql",tmp_path)
    with pytest.raises(ValueError,match="must be sqlite or postgresql"):
        build_persistence("other",tmp_path)


def test_postgres_selection_migrates_and_builds_stores(monkeypatch,tmp_path):
    import meemee_persist_pg
    events=[]
    class DB:
        def __init__(self,dsn): events.append(("db",dsn))
        def close(self): events.append(("close",))
    class Migrate:
        def __init__(self,db): events.append(("migrate-init",db))
        def apply(self): events.append(("migrate",))
    class Store:
        def __init__(self,db): self.db=db
    monkeypatch.setattr(meemee_persist_pg,"Database",DB)
    monkeypatch.setattr(meemee_persist_pg,"MigrationStore",Migrate)
    monkeypatch.setattr(meemee_persist_pg,"MemoryStore",Store)
    monkeypatch.setattr(meemee_persist_pg,"JobStore",Store)
    selected=build_persistence("postgresql",tmp_path,"postgresql://test")
    assert selected.backend=="postgresql" and ("migrate",) in events
    selected.close(); assert events[-1]==("close",)


def test_postgres_job_store_declares_owner_scoped_api_contract():
    source=(__import__("pathlib").Path(__file__).parents[1]/"meemee_persist_pg/jobs.py").read_text()
    migration=(__import__("pathlib").Path(__file__).parents[1]/"meemee_persist_pg/sql/002_job_ownership.sql").read_text()
    assert "def list_for_principal" in source and "def get_owned" in source and "principal" in migration
