import json

import pytest

from meemee.account_export import export_account, inspect_import
from meemee.entitlements import EntitlementStore
from meemee.jobs import JobStore
from meemee.runs import RunStore
from meemee.types import RunReport


def test_account_export_is_owner_scoped_and_verified(tmp_path):
    JobStore(tmp_path/"jobs.sqlite3").enqueue("mine",principal="u")
    JobStore(tmp_path/"jobs.sqlite3").enqueue("other",principal="v")
    runs=RunStore(tmp_path/"runs.sqlite3"); runs.add("u",RunReport(run_id="r",goal="g",final="f",steps_used=1,tool_results=[]))
    EntitlementStore(tmp_path/"entitlements.sqlite3").assign("u","team","2026-09-22T00:00:00Z")
    target=tmp_path/"export.json"; report=export_account(tmp_path,"u",target)
    assert report["jobs"]==1 and report["runs"]==1 and report["entitlements"]==1
    inspected=inspect_import(target,"new-u")
    assert inspected["target_principal"]=="new-u" and inspected["payload"]["jobs"][0]["goal"]=="mine"
    assert "other" not in target.read_text()


def test_export_refuses_overwrite_and_checksum_tampering(tmp_path):
    target=tmp_path/"export.json"; export_account(tmp_path,"u",target)
    with pytest.raises(FileExistsError): export_account(tmp_path,"u",target)
    data=json.loads(target.read_text()); data["payload"]["principal"]="attacker"; target.write_text(json.dumps(data))
    with pytest.raises(ValueError,match="checksum"): inspect_import(target)


def test_account_import_retargets_and_refuses_collisions(tmp_path):
    from meemee.account_export import import_account
    source_dir=tmp_path/"source"; source_dir.mkdir()
    JobStore(source_dir/"jobs.sqlite3").enqueue("mine",principal="u")
    runs=RunStore(source_dir/"runs.sqlite3"); runs.add("u",RunReport(run_id="r",goal="g",final="f",steps_used=1,tool_results=[]))
    EntitlementStore(source_dir/"entitlements.sqlite3").assign("u","team","2026-09-22T00:00:00Z")
    export=tmp_path/"export.json"; export_account(source_dir,"u",export)
    target=tmp_path/"target"; target.mkdir(); JobStore(target/"jobs.sqlite3"); RunStore(target/"runs.sqlite3"); EntitlementStore(target/"entitlements.sqlite3")
    report=import_account(target,export,"new")
    assert report["target_principal"]=="new"
    assert JobStore(target/"jobs.sqlite3").list_for_principal("new")[0][0]["goal"]=="mine"
    assert RunStore(target/"runs.sqlite3").get("new","r")["final"]=="f"
    assert EntitlementStore(target/"entitlements.sqlite3").get("new")["plan"]=="team"
    with pytest.raises(ValueError,match="collision"): import_account(target,export,"newer")
