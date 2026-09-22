import sqlite3

import pytest

from meemee.schema_registry import register_schema, schema_status


def test_schema_registration_is_idempotent_and_visible():
    db=sqlite3.connect(":memory:")
    register_schema(db,"jobs",2,["a","b"]); register_schema(db,"jobs",2,["a","b"])
    status=schema_status(db)
    assert status[0]["component"]=="jobs" and status[0]["version"]==2
    assert len(status[0]["checksum"])==64


def test_schema_registration_detects_drift_and_newer_database():
    db=sqlite3.connect(":memory:"); register_schema(db,"jobs",2,["a"])
    with pytest.raises(RuntimeError,match="checksum"): register_schema(db,"jobs",2,["changed"])
    with pytest.raises(RuntimeError,match="newer"): register_schema(db,"jobs",1,["a"])
