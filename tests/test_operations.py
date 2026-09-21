import json
import sqlite3
from pathlib import Path

import pytest

from meemee.backup import BackupManager
from meemee.migrations import Migration, Migrator


def test_migrations_are_idempotent_and_checksummed(tmp_path: Path):
    db = tmp_path / "db.sqlite3"
    migrations = [Migration(1, "create-items", "CREATE TABLE items(id INTEGER PRIMARY KEY);")]
    assert Migrator(db, migrations).migrate() == [1]
    assert Migrator(db, migrations).migrate() == []
    with pytest.raises(RuntimeError, match="checksum changed"):
        Migrator(db, [Migration(1, "changed", "CREATE TABLE other(id INTEGER);")]).migrate()


def test_migration_failure_rolls_back_version(tmp_path: Path):
    db = tmp_path / "db.sqlite3"
    with pytest.raises(sqlite3.Error):
        Migrator(db, [Migration(1, "bad", "THIS IS NOT SQL;")]).migrate()
    connection = sqlite3.connect(db)
    assert connection.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == 0


def test_backup_online_copy_manifest_and_verify(tmp_path: Path):
    data = tmp_path / "data"; data.mkdir()
    db = sqlite3.connect(data / "jobs.sqlite3")
    db.execute("CREATE TABLE jobs(id INTEGER PRIMARY KEY, value TEXT)")
    db.execute("INSERT INTO jobs(value) VALUES('real')"); db.commit(); db.close()
    destination = tmp_path / "backup"
    manifest = BackupManager(data).create(destination)
    assert manifest["files"][0]["name"] == "jobs.sqlite3"
    assert BackupManager.verify(destination)["format"] == 1
    restored = sqlite3.connect(destination / "jobs.sqlite3")
    assert restored.execute("SELECT value FROM jobs").fetchone()[0] == "real"


def test_backup_detects_tampering(tmp_path: Path):
    data = tmp_path / "data"; data.mkdir()
    db = sqlite3.connect(data / "x.sqlite3"); db.execute("CREATE TABLE x(id INT)"); db.close()
    destination = tmp_path / "backup"; BackupManager(data).create(destination)
    manifest = json.loads((destination / "manifest.json").read_text())
    (destination / manifest["files"][0]["name"]).write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="wrong size|checksum"):
        BackupManager.verify(destination)
