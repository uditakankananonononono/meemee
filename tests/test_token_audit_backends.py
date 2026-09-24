"""API tokens, accounts and the audit chain: one contract, two backends.

Runs on SQLite always and on PostgreSQL when MEEMEE_TEST_POSTGRES_DSN (or
MEEMEE_TEST_DATABASE_URL) is set, each in its own throwaway database.
"""
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from itertools import pairwise

import pytest

from meemee.persistence import build_persistence

PG_DSN = os.getenv("MEEMEE_TEST_POSTGRES_DSN") or os.getenv("MEEMEE_TEST_DATABASE_URL")
BACKENDS = ["sqlite", pytest.param("postgresql", marks=pytest.mark.skipif(not PG_DSN, reason="requires real PostgreSQL"))]


def _pg_dsn():
    import psycopg
    from psycopg.conninfo import make_conninfo

    name = "meemee_tok_" + uuid.uuid4().hex[:12]
    with psycopg.connect(PG_DSN, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')

    def drop():
        with psycopg.connect(PG_DSN, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    return make_conninfo(PG_DSN, dbname=name), drop


@pytest.fixture(params=BACKENDS)
def persistence(request, tmp_path):
    if request.param == "sqlite":
        value = build_persistence("sqlite", tmp_path)
        yield value
        value.close()
        return
    dsn, drop = _pg_dsn()
    value = build_persistence("postgresql", tmp_path, dsn)
    value.dsn = dsn
    try:
        yield value
    finally:
        value.close()
        drop()


def test_both_stores_satisfy_the_interfaces(persistence):
    from meemee_persist_pg.interfaces import AuditStoreInterface, TokenStoreInterface

    assert isinstance(persistence.tokens, TokenStoreInterface)
    assert isinstance(persistence.audit, AuditStoreInterface)
    assert persistence.tokens.ping() and persistence.audit.ping()


def test_token_lifecycle_and_owner_scoping(persistence):
    tokens = persistence.tokens
    ident, raw = tokens.create("ci", {"jobs:read", "runs:write"})
    principal = tokens.authenticate(raw)
    assert principal.id == ident and principal.scopes == frozenset({"jobs:read", "runs:write"})
    owned_id, owned = tokens.create("key", {"jobs:read"}, owner_id="acct_x")
    assert tokens.authenticate(owned).id == "acct_x"
    record = tokens.introspect(owned)
    assert record["state"] == "active" and record["principal"] == "acct_x" and record["token_kind"] == "api"
    assert record["created_at"].endswith("+00:00") and record["last_used_at"].endswith("+00:00")  # UTC on both backends
    assert not tokens.revoke(owned_id, owner_id="someone-else")
    assert tokens.revoke(owned_id, owner_id="acct_x") and not tokens.revoke(owned_id)
    assert tokens.authenticate(owned) is None and tokens.introspect(owned)["state"] == "revoked"
    assert tokens.authenticate("mee_never_issued") is None and tokens.introspect("mee_never_issued") is None


def test_token_expiry_is_parsed_and_enforced(persistence):
    tokens = persistence.tokens
    _, expired = tokens.create("old", {"jobs:read"}, (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
    assert tokens.authenticate(expired) is None and tokens.introspect(expired)["state"] == "expired"
    _, later = tokens.create("later", {"jobs:read"}, "2999-01-01T00:00:00Z")
    assert tokens.authenticate(later) is not None
    with pytest.raises(ValueError):
        tokens.create("bad", {"jobs:read"}, "never")


def test_token_listing_pages_with_cursor_and_filters(persistence):
    tokens = persistence.tokens
    made = [tokens.create(f"t{i}", {"jobs:read"}, owner_id="acct_p")[0] for i in range(5)]
    tokens.create("other", {"jobs:read"}, owner_id="acct_q")
    tokens.revoke(made[0])
    seen, cursor = [], None
    while True:
        page, cursor = tokens.list_metadata(limit=2, cursor=cursor, owner_id="acct_p")
        seen.extend(page)
        if cursor is None:
            break
    assert sorted(item["id"] for item in seen) == sorted(made) and len(seen) == 5
    assert set(seen[0]) == {"id", "name", "scopes", "created_at", "last_used_at", "expires_at", "revoked_at"}
    assert all(isinstance(item["created_at"], str) and item["scopes"] == ["jobs:read"] for item in seen)
    active, _ = tokens.list_metadata(revoked=False, owner_id="acct_p")
    assert made[0] not in {item["id"] for item in active} and len(active) == 4


def test_account_login_lockout_reset_and_disable(persistence):
    tokens = persistence.tokens
    account, session = tokens.create_account(" Live@Example.com ", "correct horse battery", " Live ")
    assert account["email"] == "live@example.com" and account["display_name"] == "Live" and isinstance(account["created_at"], str)
    assert tokens.authenticate(session).id == account["id"]
    assert tokens.introspect(session)["token_kind"] == "session"
    with pytest.raises(ValueError, match="already exists"):
        tokens.create_account("live@example.com", "correct horse battery", "Again")
    assert tokens.account_by_email("LIVE@example.com")["id"] == account["id"] == tokens.get_account(account["id"])["id"]
    logged = tokens.login_account("live@example.com", "correct horse battery")
    assert logged is not None and logged[0] == account
    for _ in range(5):
        assert tokens.login_account("live@example.com", "wrong password!!") is None
    assert tokens.login_account("live@example.com", "correct horse battery") is None  # locked
    assert tokens.reset_password(account["id"], "a brand new passphrase")
    assert tokens.authenticate(session) is None  # reset revokes existing sessions
    _, new_session = tokens.login_account("live@example.com", "a brand new passphrase")
    assert tokens.disable_account(account["id"]) and not tokens.disable_account(account["id"])
    assert tokens.authenticate(new_session) is None and tokens.get_account(account["id"]) is None
    assert tokens.login_account("live@example.com", "a brand new passphrase") is None


def test_audit_chain_append_verify_page_and_tamper(persistence):
    audit = persistence.audit
    seqs = [audit.append("actor", "write", f"res-{i}", "success", {"n": i, "note": "caf\u00e9"}) for i in range(7)]
    assert seqs == sorted(seqs) and len(set(seqs)) == 7
    assert audit.verify() == (True, None)
    page, cursor = audit.list_page(0, 3)
    assert [e["resource"] for e in page] == ["res-0", "res-1", "res-2"] and cursor == str(page[-1]["sequence"])
    assert page[0]["metadata"] == {"n": 0, "note": "caf\u00e9"} and isinstance(page[0]["occurred_at"], str)
    rest, end = audit.list_page(int(cursor), 10)
    assert len(rest) == 4 and end is None
    assert rest[0]["previous_hash"] == page[-1]["entry_hash"]
    if persistence.backend == "sqlite":
        with persistence.audit.db:
            persistence.audit.db.execute("UPDATE audit_log SET resource='forged' WHERE sequence=?", (seqs[3],))
    else:
        with persistence.database.transaction() as c:
            c.execute("UPDATE meemee_audit_log SET resource='forged' WHERE sequence=%s", (seqs[3],))
    assert audit.verify() == (False, seqs[3])


@pytest.mark.skipif(not PG_DSN, reason="requires real PostgreSQL")
def test_postgresql_chain_stays_single_under_concurrent_appends_from_separate_pools(tmp_path):
    """Two connection pools stand in for two hosts; 4 threads each append at once."""
    from meemee_persist_pg import AuditLog, Database, MigrationStore

    dsn, drop = _pg_dsn()
    pools = [Database(dsn, min_size=1, max_size=4), Database(dsn, min_size=1, max_size=4)]
    try:
        MigrationStore(pools[0]).apply()
        logs = [AuditLog(pool) for pool in pools]
        errors: list[Exception] = []

        def work(log, host, worker):
            try:
                for i in range(15):
                    log.append(f"host-{host}", "write", f"{host}-{worker}-{i}", "success", {"i": i})
            except Exception as exc:  # noqa: BLE001 - surfaced by the assertion below
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(logs[h], h, w)) for h in range(2) for w in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        assert errors == []
        assert logs[0].verify() == (True, None) and logs[1].verify() == (True, None)
        entries = logs[1].list(0, 500)
        assert len(entries) == 120
        assert all(later["previous_hash"] == earlier["entry_hash"] for earlier, later in pairwise(entries))
        assert {e["actor_id"] for e in entries} == {"host-0", "host-1"}
    finally:
        for pool in pools:
            pool.close()
        drop()


@pytest.mark.skipif(not PG_DSN, reason="requires real PostgreSQL")
def test_postgresql_chain_base_links_the_first_row_and_is_enforced(tmp_path):
    from meemee_persist_pg import AuditLog, Database, MigrationStore

    dsn, drop = _pg_dsn()
    db = Database(dsn, min_size=1, max_size=2)
    try:
        MigrationStore(db).apply()
        with db.transaction() as c:
            c.execute("INSERT INTO meemee_audit_chain_base(singleton,sequence,entry_hash) VALUES(1,41,%s)", ("a" * 64,))
            c.execute("SELECT setval(pg_get_serial_sequence('meemee_audit_log','sequence'),41)")
        log = AuditLog(db)
        first = log.append("a", "x", "r", "ok")
        assert first == 42 and log.list()[0]["previous_hash"] == "a" * 64
        assert log.verify() == (True, None)
        with db.transaction() as c:
            c.execute("UPDATE meemee_audit_chain_base SET entry_hash=%s", ("b" * 64,))
        assert log.verify() == (False, 42)
    finally:
        db.close()
        drop()
