"""Paired SQLite handles must all close when a later open fails (area 207)."""
import socket
import sqlite3
import types

import pytest

from meemee import backup as backup_mod
from meemee import key_rotation as rot
from meemee.backup import BackupManager
from meemee.vault import SecretVault
from meemee.webhooks import WebhookStore


class _Tracked:
    def __init__(self, conn):
        self.conn, self.closed = conn, False

    def close(self):
        self.closed = True
        self.conn.close()

    def backup(self, other):
        self.conn.backup(other.conn if isinstance(other, _Tracked) else other)

    def __getattr__(self, name):
        return getattr(self.conn, name)


def _patch(monkeypatch, module, fail_on):
    opened = []

    def connect(*args, **kwargs):
        if len(opened) + 1 == fail_on:
            raise sqlite3.OperationalError("injected open failure")
        tracked = _Tracked(sqlite3.connect(*args, **kwargs))
        opened.append(tracked)
        return tracked

    monkeypatch.setattr(module, "sqlite3", types.SimpleNamespace(
        connect=connect, OperationalError=sqlite3.OperationalError, Row=sqlite3.Row))
    return opened


def _seed(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a: [(2, 1, 6, "", ("93.184.216.34", 443))])
    old = SecretVault.generate_key()
    SecretVault(tmp_path / "vault.sqlite3", old).put("k", "v")
    hooks = WebhookStore(tmp_path / "webhooks.sqlite3", encryption_key=old)
    hooks.subscribe("u", "https://x.example/h", {"*"})
    hooks.db.close()
    return old


def test_rotation_backup_target_open_failure_closes_source(tmp_path, monkeypatch):
    (tmp_path / "a.sqlite3").write_bytes(b"")
    sqlite3.connect(tmp_path / "a.sqlite3").close()
    opened = _patch(monkeypatch, rot, fail_on=2)
    with pytest.raises(sqlite3.OperationalError):
        rot._backup(tmp_path / "a.sqlite3", tmp_path / "b.sqlite3")
    assert opened and all(h.closed for h in opened)


def test_rotation_restore_target_open_failure_closes_source(tmp_path, monkeypatch):
    sqlite3.connect(tmp_path / "a.sqlite3").close()
    opened = _patch(monkeypatch, rot, fail_on=2)
    with pytest.raises(sqlite3.OperationalError):
        rot._restore(tmp_path / "a.sqlite3", tmp_path / "b.sqlite3")
    assert opened and all(h.closed for h in opened)


def test_rotate_keys_second_open_failure_closes_first_and_clears_marker(tmp_path, monkeypatch):
    old = _seed(tmp_path, monkeypatch)
    new = SecretVault.generate_key()
    # 2 paths x 2 connects in backups = 4 opens; the 5th is vault, 6th is hooks.
    opened = _patch(monkeypatch, rot, fail_on=6)
    with pytest.raises(sqlite3.OperationalError):
        rot.rotate_keys(tmp_path, old, new)
    assert all(h.closed for h in opened)
    assert not (tmp_path / ".key-rotation-in-progress").exists()
    monkeypatch.undo()
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a: [(2, 1, 6, "", ("93.184.216.34", 443))])
    assert rot.rotate_keys(tmp_path, old, new) == {"vault_secrets": 1, "webhook_secrets": 1}


def test_backup_create_target_open_failure_closes_source(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    sqlite3.connect(data / "a.sqlite3").close()
    opened = _patch(monkeypatch, backup_mod, fail_on=2)
    with pytest.raises(sqlite3.OperationalError):
        BackupManager(data).create(tmp_path / "out")
    assert opened and all(h.closed for h in opened)


def test_backup_restore_target_open_failure_closes_source(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    sqlite3.connect(data / "a.sqlite3").close()
    BackupManager(data).create(tmp_path / "out")
    opened = _patch(monkeypatch, backup_mod, fail_on=3)  # 1 verify, 2 restore source, 3 restore target
    with pytest.raises(sqlite3.OperationalError):
        BackupManager(data).restore(tmp_path / "out", tmp_path / "dest")
    assert opened and all(h.closed for h in opened)
