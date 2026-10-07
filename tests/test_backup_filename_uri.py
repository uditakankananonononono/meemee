"""SQLite filenames must remain filenames, never URI query/fragment syntax."""

import sqlite3

import pytest

from meemee.backup import BackupManager


@pytest.mark.parametrize('name', ['tenant?mode=rw.sqlite3', 'tenant#section.sqlite3'])
def test_backup_and_restore_preserve_uri_special_filename(tmp_path, name):
    source = tmp_path / 'source'
    source.mkdir()
    with sqlite3.connect(source / name) as db:
        db.execute('CREATE TABLE evidence(value TEXT)')
        db.execute("INSERT INTO evidence VALUES('preserved')")
    backup = tmp_path / 'backup'
    manager = BackupManager(source)
    manifest = manager.create(backup)
    assert manifest['files'][0]['name'] == name
    BackupManager.verify(backup)
    restored = tmp_path / 'restored'
    BackupManager.restore(backup, restored)
    with sqlite3.connect(restored / name) as db:
        assert db.execute('SELECT value FROM evidence').fetchone()[0] == 'preserved'
