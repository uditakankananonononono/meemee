"""Cutover snapshots must read the named SQLite file, not a URI-truncated decoy."""

import sqlite3

import pytest

from meemee_persist_pg.cutover import sqlite_snapshot


@pytest.mark.parametrize('delimiter', ['?', '#'])
def test_cutover_snapshot_escapes_filename(tmp_path, delimiter):
    decoy = tmp_path / 'source'
    actual = tmp_path / f'source{delimiter}part.sqlite3'
    for path, value in [(decoy, 'decoy'), (actual, 'actual')]:
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE marker(value TEXT)')
            db.execute('INSERT INTO marker VALUES(?)', (value,))
    with sqlite_snapshot(actual) as db:
        assert db.execute('SELECT value FROM marker').fetchone()[0] == 'actual'
