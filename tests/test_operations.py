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


def test_backup_captures_all_commercial_state_databases(tmp_path: Path):
    data=tmp_path/"data"; data.mkdir()
    for name in ("runs.sqlite3","entitlements.sqlite3","approvals.sqlite3","webhooks.sqlite3"):
        db=sqlite3.connect(data/name); db.execute("CREATE TABLE state(value TEXT)"); db.execute("INSERT INTO state VALUES('kept')"); db.commit(); db.close()
    manifest=BackupManager(data).create(tmp_path/"commercial-backup")
    assert {row["name"] for row in manifest["files"]}=={"runs.sqlite3","entitlements.sqlite3","approvals.sqlite3","webhooks.sqlite3"}
    for row in manifest["files"]:
        restored=sqlite3.connect(tmp_path/"commercial-backup"/row["name"])
        assert restored.execute("SELECT value FROM state").fetchone()[0]=="kept"
        restored.close()


def test_backup_restore_rehearsal_refuses_overwrite_and_preserves_checksums(tmp_path: Path):
    data=tmp_path/"data"; data.mkdir()
    db=sqlite3.connect(data/"state.sqlite3"); db.execute("CREATE TABLE state(value TEXT)"); db.execute("INSERT INTO state VALUES('real')"); db.commit(); db.close()
    backup=tmp_path/"backup"; BackupManager(data).create(backup)
    restored=tmp_path/"restored"; report=BackupManager.restore(backup,restored)
    assert report["status"]=="restored" and report["files"]==["state.sqlite3"]
    check=sqlite3.connect(restored/"state.sqlite3"); assert check.execute("SELECT value FROM state").fetchone()[0]=="real"; check.close()
    with pytest.raises(FileExistsError): BackupManager.restore(backup,restored)


def test_backup_restore_refuses_tampered_source_before_destination(tmp_path: Path):
    data=tmp_path/"data"; data.mkdir(); db=sqlite3.connect(data/"x.sqlite3"); db.execute("CREATE TABLE x(id INT)"); db.close()
    backup=tmp_path/"backup"; BackupManager(data).create(backup); (backup/"x.sqlite3").write_bytes(b"bad")
    with pytest.raises(RuntimeError): BackupManager.restore(backup,tmp_path/"restored")
    assert not (tmp_path/"restored").exists()
