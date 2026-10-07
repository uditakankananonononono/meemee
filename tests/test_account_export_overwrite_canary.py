"""Export must not overwrite a destination created after its first existence check."""
from pathlib import Path

import pytest

from meemee.account_export import export_account


def test_export_concurrent_destination_creation_is_preserved(tmp_path, monkeypatch):
    destination = tmp_path / 'export.json'
    real_exists = Path.exists
    injected = [False]

    def raced_exists(path):
        if path == destination and not injected[0]:
            injected[0] = True
            destination.write_text('competing owner file')
            return False
        return real_exists(path)

    monkeypatch.setattr(Path, 'exists', raced_exists)
    with pytest.raises(FileExistsError):
        export_account(tmp_path, 'fixture-owner', destination)
    assert destination.read_text() == 'competing owner file'
