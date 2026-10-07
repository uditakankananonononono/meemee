"""A second rotation cannot overwrite a marker that appeared during its backup."""

import pytest

from meemee import key_rotation
from meemee.vault import SecretVault


def test_rotation_marker_race_preserves_existing_owner(tmp_path, monkeypatch):
    old, new = SecretVault.generate_key(), SecretVault.generate_key()
    SecretVault(tmp_path / 'vault.sqlite3', old).put('dummy', 'fixture')
    marker = tmp_path / '.key-rotation-in-progress'
    backup = key_rotation._backup

    def competing_rotation(database, destination):
        backup(database, destination)
        marker.write_text('another rotation owns this marker')

    monkeypatch.setattr(key_rotation, '_backup', competing_rotation)
    with pytest.raises((FileExistsError, RuntimeError)):
        key_rotation.rotate_keys(tmp_path, old, new)
    assert marker.read_text() == 'another rotation owns this marker'
    assert SecretVault(tmp_path / 'vault.sqlite3', old).get('dummy') == 'fixture'
