"""Integrity checks inspect the literal filename rather than a truncated URI."""

import sqlite3

import pytest

from meemee.preflight import _check_databases


@pytest.mark.parametrize('name', ['corrupt?mode=rw.sqlite3', 'corrupt#section.sqlite3'])
def test_corrupt_special_filename_cannot_pass_integrity(tmp_path, name):
    # Valid decoy at the URI-truncated path must not be mistaken for this corrupt file.
    decoy = tmp_path / 'corrupt'
    with sqlite3.connect(decoy) as db:
        db.execute('CREATE TABLE decoy(id INTEGER)')
    (tmp_path / name).write_bytes(b'not a SQLite database')
    [result] = _check_databases(tmp_path)
    assert result['name'] == 'database_integrity:' + name
    assert result['ok'] is False
    assert result['error'] == 'DatabaseError'
