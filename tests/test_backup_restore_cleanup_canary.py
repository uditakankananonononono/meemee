"""A failed restore must remove the current partially written database too."""
import sqlite3

import pytest

from meemee import backup
from meemee.backup import BackupManager


def test_restore_cleans_current_file_on_copy_failure(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    data.mkdir()
    with sqlite3.connect(data / 'first.sqlite3') as db:
        db.execute('CREATE TABLE fixture(n INTEGER)')
    folder = tmp_path / 'backup'
    BackupManager(data).create(folder)
    BackupManager.verify(folder)
    connect = sqlite3.connect
    # Allow verification to finish first, then inject only the restore copy fault.
    monkeypatch.setattr(BackupManager, 'verify', staticmethod(lambda path: __import__('json').loads((path / 'manifest.json').read_text())))

    class BrokenSource:
        def backup(self, target):
            target.execute('CREATE TABLE partial(n INTEGER)')
            target.commit()
            raise RuntimeError('fixture copy failed midway')

        def close(self):
            pass

    def fault_connect(path, *args, **kwargs):
        if kwargs.get('uri'):
            return BrokenSource()
        return connect(path, *args, **kwargs)

    monkeypatch.setattr(backup.sqlite3, 'connect', fault_connect)
    destination = tmp_path / 'restore'
    with pytest.raises(RuntimeError, match='copy failed'):
        BackupManager.restore(folder, destination)
    assert list(destination.iterdir()) == []
