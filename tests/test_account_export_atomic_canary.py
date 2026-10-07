"""A failed export write must not leave a published partial export."""
from pathlib import Path

import pytest

from meemee.account_export import export_account


def test_failed_export_write_leaves_no_destination(tmp_path, monkeypatch):
    destination = tmp_path / 'export.json'
    original = Path.open

    class Interrupted:
        def __init__(self, file):
            self.file = file
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.file.close()
        def write(self, data):
            self.file.write(data[:12])
            self.file.flush()
            raise OSError('fixture full disk')

    def fail_write(path, mode='r', *args, **kwargs):
        file = original(path, mode, *args, **kwargs)
        return Interrupted(file) if mode in {'x', 'w'} else file

    monkeypatch.setattr(Path, 'open', fail_write)
    with pytest.raises(OSError, match='full disk'):
        export_account(tmp_path, 'fixture-owner', destination)
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []
