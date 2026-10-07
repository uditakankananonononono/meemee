"""Rotation recovery helpers must back up and restore the literal database file."""

import sqlite3

import pytest

from meemee.key_rotation import _backup, _restore


@pytest.mark.parametrize('operation', ['backup', 'restore'])
def test_rotation_helper_uses_literal_uri_filename(tmp_path, operation):
    source = tmp_path / 'source#fragment.sqlite3'
    # A valid decoy makes the truncated URI silently return the wrong contents.
    with sqlite3.connect(tmp_path / 'source') as db:
        db.execute('CREATE TABLE evidence(value TEXT)')
        db.execute("INSERT INTO evidence VALUES('wrong')")
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE evidence(value TEXT)')
        db.execute("INSERT INTO evidence VALUES('right')")
    target = tmp_path / 'target.sqlite3'
    (_backup if operation == 'backup' else _restore)(source, target)
    with sqlite3.connect(target) as db:
        assert db.execute('SELECT value FROM evidence').fetchone()[0] == 'right'
