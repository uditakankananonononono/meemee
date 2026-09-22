import pytest

from meemee.loadcheck import run_loadcheck


def test_loadcheck_passes_without_lost_writes_or_audit_breaks():
    report=run_loadcheck(operations=200,workers=8)
    assert report["status"]=="pass" and report["errors"]==[]
    assert report["audit_valid"] and report["stored_jobs"]==50
    assert sum(report["counts"].values())==200


def test_loadcheck_rejects_invalid_size():
    with pytest.raises(ValueError): run_loadcheck(operations=0)
