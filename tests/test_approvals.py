"""Tool-approval grants: one contract, two backends.

Every test runs against the SQLite store and, when MEEMEE_TEST_POSTGRES_DSN (or
MEEMEE_TEST_DATABASE_URL) is set, the PostgreSQL store in its own throwaway database.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from meemee.approvals import ApprovalStore, normalize_expiry

PG_DSN = os.getenv("MEEMEE_TEST_POSTGRES_DSN") or os.getenv("MEEMEE_TEST_DATABASE_URL")
BACKENDS = ["sqlite", pytest.param("postgresql", marks=pytest.mark.skipif(not PG_DSN, reason="requires real PostgreSQL"))]


@pytest.fixture(params=BACKENDS)
def store(request, tmp_path: Path):
    if request.param == "sqlite":
        yield ApprovalStore(tmp_path / "a.db")
        return
    import psycopg
    from psycopg.conninfo import make_conninfo

    from meemee_persist_pg import ApprovalStore as PGApprovalStore
    from meemee_persist_pg import ApprovalStoreInterface, Database, MigrationStore

    name = "meemee_grants_" + uuid.uuid4().hex[:12]
    with psycopg.connect(PG_DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    database = Database(make_conninfo(PG_DSN, dbname=name), min_size=1, max_size=4)
    try:
        MigrationStore(database).apply()
        value = PGApprovalStore(database)
        assert isinstance(value, ApprovalStoreInterface)
        yield value
    finally:
        database.close()
        with psycopg.connect(PG_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def test_approval_grant_expire_revoke(store):
    store.grant("u", "git.commit", "admin")
    assert store.allows("u", "git.commit")
    assert not store.allows("other", "git.commit")
    assert store.revoke("u", "git.commit")
    assert not store.revoke("u", "git.commit")
    assert not store.allows("u", "git.commit")


def test_approval_expiry_and_regrant(store):
    past = (datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
    store.grant("u", "workspace.write_file", "admin", past)
    assert not store.allows("u", "workspace.write_file")
    store.grant("u", "workspace.write_file", "admin")
    assert store.allows("u", "workspace.write_file")
    assert store.list("u")[0]["revoked_at"] is None


def test_expiry_is_exclusive_and_now_is_injectable(store):
    at = datetime(2030, 1, 1, tzinfo=timezone.utc)
    store.grant("u", "t", "admin", at.isoformat())
    assert store.allows("u", "t", now=at - timedelta(microseconds=1))
    assert not store.allows("u", "t", now=at)
    assert store.active_count("u", now=at - timedelta(seconds=1)) == 1
    assert store.active_count("u", now=at) == 0


def test_active_count_excludes_revoked_and_expired(store):
    store.grant("u","a","admin")
    store.grant("u","expired","admin",(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat())
    store.grant("u","revoked","admin"); store.revoke("u","revoked")
    assert store.active_count("u")==1


def test_argument_scoped_approval_matches_exact_constraint_subset(store):
    store.grant("u","github.push_branch","admin",argument_constraints={"owner":"acme","repository":"safe"})
    assert store.allows("u","github.push_branch",arguments={"owner":"acme","repository":"safe","branch":"feature"})
    assert not store.allows("u","github.push_branch",arguments={"owner":"evil","repository":"safe"})
    assert not store.allows("u","github.push_branch")
    assert store.list("u")[0]["argument_constraints"] == {"owner":"acme","repository":"safe"}


def test_regrant_can_narrow_then_remove_constraints(store):
    store.grant("u","shell.command","admin",argument_constraints={"command":"pytest"})
    assert not store.allows("u","shell.command",arguments={"command":"git"})
    store.grant("u","shell.command","admin")
    assert store.allows("u","shell.command",arguments={"command":"git"})


def test_list_shape_is_identical_across_backends(store):
    store.grant("u", "b.tool", "admin", "2031-02-03T04:05:06Z", {"path": "x"})
    store.grant("u", "a.tool", "admin")
    store.revoke("u", "a.tool")
    rows = store.list("u")
    assert [r["tool"] for r in rows] == ["a.tool", "b.tool"]
    assert set(rows[0]) == {"principal", "tool", "granted_at", "expires_at", "revoked_at", "granted_by", "argument_constraints"}
    for row in rows:
        assert isinstance(row["granted_at"], str) and row["granted_at"].endswith("+00:00")
    assert rows[0]["revoked_at"] is not None and rows[0]["argument_constraints"] is None
    assert datetime.fromisoformat(rows[1]["expires_at"]) == datetime(2031, 2, 3, 4, 5, 6, tzinfo=timezone.utc)
    assert rows[1]["argument_constraints"] == {"path": "x"} and rows[1]["granted_by"] == "admin"


def test_offset_expiry_is_compared_as_an_instant(store):
    # 10:00+05:30 is 04:30 UTC. Stored as text without normalization, SQLite compared it as a string.
    store.grant("u", "t", "admin", "2030-06-01T10:00:00+05:30")
    assert store.allows("u", "t", now=datetime(2030, 6, 1, 4, 29, tzinfo=timezone.utc))
    assert not store.allows("u", "t", now=datetime(2030, 6, 1, 4, 31, tzinfo=timezone.utc))


def test_invalid_input_is_rejected(store):
    with pytest.raises(ValueError):
        store.grant("u", "t", "admin", "next tuesday")
    with pytest.raises(ValueError):
        store.grant("", "t", "admin")
    assert store.list("u") == []


def test_delete_principal_removes_only_that_principal(store):
    store.grant("u", "a", "admin"); store.grant("u", "b", "admin"); store.revoke("u", "b")
    store.grant("v", "a", "admin")
    assert store.delete_principal("u") == 2
    assert store.list("u") == [] and store.allows("v", "a")


def test_normalize_expiry():
    assert normalize_expiry(None) is None
    assert normalize_expiry("2030-01-01T00:00:00Z") == "2030-01-01T00:00:00+00:00"
    assert normalize_expiry("2030-01-01T05:30:00+05:30") == "2030-01-01T00:00:00+00:00"
    assert normalize_expiry("2030-01-01T00:00:00") == "2030-01-01T00:00:00+00:00"
    with pytest.raises(ValueError):
        normalize_expiry("soon")
