"""Unsupported backup versions and duplicate entries are not valid restore plans."""

import json
import sqlite3

import pytest

from meemee.backup import BackupManager


@pytest.mark.parametrize('fault', ['version', 'duplicate'])
def test_backup_manifest_identity_rejects_ambiguous_plan(tmp_path, fault):
    source = tmp_path / 'source'
    source.mkdir()
    with sqlite3.connect(source / 'data.sqlite3') as db:
        db.execute('CREATE TABLE evidence(id INTEGER)')
    backup = tmp_path / 'backup'
    manifest = BackupManager(source).create(backup)
    if fault == 'version':
        manifest['format'] = 900
    else:
        manifest['files'].append(dict(manifest['files'][0]))
    (backup / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='manifest'):
        BackupManager.restore(backup, tmp_path / 'restored')
    assert not (tmp_path / 'restored').exists()
