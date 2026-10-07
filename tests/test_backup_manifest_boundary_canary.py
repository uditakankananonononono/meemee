"""Backup manifests must not select databases outside their directory."""
import hashlib
import json
import sqlite3

import pytest

from meemee.backup import BackupManager


@pytest.mark.parametrize('symlink', [False, True])
def test_backup_manifest_cannot_escape_directory(tmp_path, symlink):
    outside = tmp_path / 'outside.sqlite3'
    with sqlite3.connect(outside) as db:
        db.execute('CREATE TABLE fixture(n INTEGER)')
    folder = tmp_path / 'backup'
    folder.mkdir()
    name = '../outside.sqlite3'
    if symlink:
        (folder / 'linked.sqlite3').symlink_to(outside)
        name = 'linked.sqlite3'
    (folder / 'manifest.json').write_text(json.dumps({'format': 1, 'created_at': 'fixture', 'files': [
        {'name': name, 'bytes': outside.stat().st_size, 'sha256': hashlib.sha256(outside.read_bytes()).hexdigest()}
    ]}))
    with pytest.raises(ValueError, match='backup file'):
        BackupManager.verify(folder)
