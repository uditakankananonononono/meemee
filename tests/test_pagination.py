import pytest

from meemee.auth import TokenStore
from meemee.jobs import JobStore
from meemee.runs import RunStore
from meemee.types import RunReport


def test_job_cursor_is_stable_and_non_overlapping(tmp_path):
    store=JobStore(tmp_path/"j.db")
    ids=[store.enqueue(str(i),principal="u") for i in range(3)]
    for index,ident in enumerate(ids): store.db.execute("UPDATE jobs SET updated_at=? WHERE id=?",(f"2026-01-0{index+1}T00:00:00Z",ident))
    first,cursor=store.list_for_principal("u",limit=2); second,end=store.list_for_principal("u",limit=2,cursor=cursor)
    assert len(first)==2 and len(second)==1 and {x["id"] for x in first}.isdisjoint({x["id"] for x in second}) and end is None
    with pytest.raises(ValueError,match="cursor"): store.list_for_principal("u",cursor="broken")


def test_run_and_token_cursors(tmp_path):
    runs=RunStore(tmp_path/"r.db")
    for i in range(3): runs.add("u",RunReport(run_id=f"r{i}",goal="g",final="f",steps_used=1,tool_results=[]))
    first,cursor=runs.list("u",limit=2); second,end=runs.list("u",limit=2,cursor=cursor)
    assert len(first)==2 and len(second)==1 and end is None
    tokens=TokenStore(tmp_path/"a.db")
    for i in range(3): tokens.create(f"t{i}",{"jobs:read"})
    first,cursor=tokens.list_metadata(limit=2); second,end=tokens.list_metadata(limit=2,cursor=cursor)
    assert len(first)==2 and len(second)==1 and end is None
