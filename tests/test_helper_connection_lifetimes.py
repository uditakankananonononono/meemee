"""Function-level SQLite connection lifetimes and cleanup masking (area 210).

Characterization tests (marked in names) show the helper sites already close their single
handle on failure. The masking tests show cleanup errors must not replace the original
exception, and cancellation must still roll back.
"""
import sqlite3

import pytest

from meemee import key_rotation as rot
from meemee.account_export import _rows, export_account, import_account
from meemee.entitlements import EntitlementStore
from meemee.jobs import JobStore
from meemee.memory import MemoryStore
from meemee.migrations import Migration, Migrator
from meemee.retention import RetentionManager
from meemee.runs import RunStore
from meemee.types import RunReport
from meemee.vault import SecretVault
from meemee.webhooks import WebhookStore

_real_connect = sqlite3.connect


class _Conn:
    """Delegating connection with SQL-triggered failures and rollback tracking."""

    def __init__(self, conn, fail_on=None, error=None, rollback_error=None):
        object.__setattr__(self, "_c", conn)
        object.__setattr__(self, "_fail_on", fail_on)
        object.__setattr__(self, "_error", error)
        object.__setattr__(self, "_rollback_error", rollback_error)
        object.__setattr__(self, "closed", False)
        object.__setattr__(self, "rollbacks", 0)

    def _check(self, sql):
        if self._fail_on and self._fail_on in str(sql):
            raise self._error

    def execute(self, sql, *a, **k):
        self._check(sql)
        return self._c.execute(sql, *a, **k)

    def executemany(self, sql, *a, **k):
        self._check(sql)
        return self._c.executemany(sql, *a, **k)

    def executescript(self, sql):
        self._check(sql)
        return self._c.executescript(sql)

    def backup(self, target, **kw):
        return self._c.backup(target._c if isinstance(target, _Conn) else target, **kw)

    def rollback(self):
        object.__setattr__(self, "rollbacks", self.rollbacks + 1)
        if self._rollback_error is not None:
            raise self._rollback_error
        return self._c.rollback()

    def close(self):
        object.__setattr__(self, "closed", True)
        self._c.close()

    def __enter__(self):
        return self._c.__enter__()

    def __exit__(self, *exc):
        return self._c.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._c, name)

    def __setattr__(self, name, value):
        setattr(self._c, name, value)


@pytest.fixture
def track(monkeypatch):
    """Patch sqlite3.connect; returns configure(fail_on, error, rollback_error) -> opened list."""
    opened: list[_Conn] = []

    def configure(fail_on=None, error=None, rollback_error=None, only_nth=None):
        def connect(*args, **kwargs):
            conn = _Conn(
                _real_connect(*args, **kwargs),
                fail_on if only_nth is None or len(opened) + 1 == only_nth else None,
                error, rollback_error,
            )
            opened.append(conn)
            return conn

        monkeypatch.setattr(sqlite3, "connect", connect)
        return opened

    return configure


BOOM = sqlite3.OperationalError("injected failure")


def _plain_db(path):
    db = _real_connect(path)
    db.execute("CREATE TABLE t(a)")
    db.commit()
    db.close()


# ---- characterization: helpers already close their one handle on failure ----

def test_characterization_retention_delete_closes_on_failure(tmp_path, track):
    _plain_db(tmp_path / "x.sqlite3")
    opened = track("DELETE", BOOM)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        RetentionManager.delete(tmp_path / "x.sqlite3", "DELETE FROM t", ())
    assert opened and all(c.closed for c in opened)


def test_characterization_retention_private_deletes_close_on_failure(tmp_path, track):
    JobStore(tmp_path / "jobs.sqlite3")
    MemoryStore(tmp_path / "memory.sqlite3")
    WebhookStore(tmp_path / "webhooks.sqlite3", encryption_key=SecretVault.generate_key())
    manager = RetentionManager(tmp_path)
    calls = [
        lambda: manager._delete_jobs(tmp_path / "jobs.sqlite3", "9999"),
        lambda: manager._delete_memories(tmp_path / "memory.sqlite3", "9999"),
        lambda: manager._delete_webhooks(tmp_path / "webhooks.sqlite3", "done", "9999"),
    ]
    for call in calls:
        opened = track("DELETE", BOOM)
        with pytest.raises(sqlite3.OperationalError, match="injected"):
            call()
        assert opened and all(c.closed for c in opened)


def test_characterization_postgres_copy_export_closes_on_failure(tmp_path, track):
    from meemee.postgres_copy import TABLES, export_sqlite

    _plain_db(tmp_path / TABLES["memories"][0])
    opened = track("sqlite_master", BOOM)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        export_sqlite(tmp_path, tmp_path / "out" / "export.json")
    assert opened and all(c.closed for c in opened)


def test_characterization_preflight_closes_and_reports_failure(tmp_path, track):
    from meemee.preflight import _check_databases

    _plain_db(tmp_path / "x.sqlite3")
    opened = track("quick_check", BOOM)
    checks = _check_databases(tmp_path)
    assert opened and all(c.closed for c in opened)
    assert checks and checks[0]["ok"] is False


def test_characterization_account_rows_closes_on_failure(tmp_path, track):
    _plain_db(tmp_path / "x.sqlite3")
    opened = track("SELECT", BOOM)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        _rows(tmp_path / "x.sqlite3", "SELECT * FROM t", ())
    assert opened and all(c.closed for c in opened)


def test_characterization_cli_schema_status_closes_on_failure(tmp_path, track, monkeypatch):
    from meemee.cli import schema_status_command

    _plain_db(tmp_path / "x.sqlite3")
    monkeypatch.setenv("MEEMEE_DATA_DIR", str(tmp_path))
    opened = track("component_schema_versions", BOOM)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        schema_status_command()
    assert opened and all(c.closed for c in opened)


# ---- masking: cleanup errors must not replace the original failure ----

def test_migrate_rollback_failure_keeps_original_error(tmp_path, track):
    migrator = Migrator(tmp_path / "m.sqlite3", [Migration(1, "one", "CREATE TABLE a(x);")])
    opened = track("INSERT INTO schema_migrations", BOOM, rollback_error=RuntimeError("rb"))
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        migrator.migrate()
    assert opened and all(c.closed for c in opened)


def _import_fixture(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    JobStore(source / "jobs.sqlite3").enqueue("mine", principal="u")
    RunStore(source / "runs.sqlite3").add(
        "u", RunReport(run_id="r", goal="g", final="f", steps_used=1, tool_results=[])
    )
    EntitlementStore(source / "entitlements.sqlite3").assign("u", "team", "2026-09-22T00:00:00Z")
    export = tmp_path / "export.json"
    export_account(source, "u", export)
    target = tmp_path / "target"
    target.mkdir()
    JobStore(target / "jobs.sqlite3")
    RunStore(target / "runs.sqlite3")
    EntitlementStore(target / "entitlements.sqlite3")
    return target, export


def test_import_account_rollback_failure_keeps_original_error(tmp_path, track):
    target, export = _import_fixture(tmp_path)
    opened = track(
        "INSERT INTO imported_entitlements", BOOM, rollback_error=RuntimeError("rb")
    )
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        import_account(target, export, "new")
    assert opened and all(c.closed for c in opened)


def test_import_account_rolls_back_on_keyboard_interrupt(tmp_path, track):
    target, export = _import_fixture(tmp_path)
    opened = track("INSERT INTO imported_entitlements", KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        import_account(target, export, "new")
    assert opened and all(c.closed for c in opened)
    assert opened[0].rollbacks == 1
    jobs = _real_connect(target / "jobs.sqlite3").execute("SELECT count(*) FROM jobs").fetchone()[0]
    assert jobs == 0


def _rotation_fixture(tmp_path, monkeypatch):
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a: [(2, 1, 6, "", ("93.184.216.34", 443))])
    old = SecretVault.generate_key()
    SecretVault(tmp_path / "vault.sqlite3", old).put("k", "v")
    hooks = WebhookStore(tmp_path / "webhooks.sqlite3", encryption_key=old)
    hooks.subscribe("u", "https://x.example/h", {"*"})
    hooks.db.close()
    return old


def test_rotate_keys_rollback_failure_before_mutation_keeps_original_error(
    tmp_path, track, monkeypatch
):
    from cryptography.exceptions import InvalidTag

    _rotation_fixture(tmp_path, monkeypatch)
    opened = track(rollback_error=RuntimeError("rb"))
    with pytest.raises(InvalidTag):
        rot.rotate_keys(tmp_path, SecretVault.generate_key(), SecretVault.generate_key())
    assert all(c.closed for c in opened)
    assert not (tmp_path / ".key-rotation-in-progress").exists()


def test_rotate_keys_rollback_failure_after_mutation_still_restores_and_clears_marker(
    tmp_path, track, monkeypatch
):
    old = _rotation_fixture(tmp_path, monkeypatch)
    restored = []
    real_restore = rot._restore
    monkeypatch.setattr(rot, "_restore", lambda b, d: (restored.append(d.name), real_restore(b, d)))
    opened = track("UPDATE webhook_subscriptions", BOOM, rollback_error=RuntimeError("rb"))
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        rot.rotate_keys(tmp_path, old, SecretVault.generate_key())
    assert all(c.closed for c in opened)
    assert sorted(restored) == ["vault.sqlite3", "webhooks.sqlite3"]
    assert not (tmp_path / ".key-rotation-in-progress").exists()
    assert SecretVault(tmp_path / "vault.sqlite3", old).get("k") == "v"
