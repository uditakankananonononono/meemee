"""Regression canaries: failed migrations must not publish partial state."""
import sqlite3

import pytest

from meemee.migrations import Migration, Migrator


def test_failed_ddl_rolls_back_schema(tmp_path):
    db = tmp_path / 'ddl.db'
    with pytest.raises(sqlite3.Error):
        Migrator(db, [Migration(1, 'bad-ddl', 'CREATE TABLE leaked(id INT); NOT SQL;')]).migrate()
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='leaked'").fetchone() is None
        assert connection.execute('SELECT count(*) FROM schema_migrations').fetchone()[0] == 0


def test_failed_dml_rolls_back_data(tmp_path):
    db = tmp_path / 'dml.db'
    initial = Migration(1, 'initial', 'CREATE TABLE items(value TEXT); INSERT INTO items VALUES(\'original\');')
    Migrator(db, [initial]).migrate()
    with pytest.raises(sqlite3.Error):
        Migrator(db, [initial, Migration(2, 'bad-dml', "UPDATE items SET value='leaked'; NOT SQL;")]).migrate()
    with sqlite3.connect(db) as connection:
        assert connection.execute('SELECT value FROM items').fetchone()[0] == 'original'
        assert connection.execute('SELECT version FROM schema_migrations').fetchall() == [(1,)]


def test_script_cannot_commit_before_failure(tmp_path):
    db = tmp_path / 'commit.db'
    with pytest.raises(sqlite3.Error):
        Migrator(db, [Migration(1, 'commit', 'CREATE TABLE leaked(id INT); COMMIT; NOT SQL;')]).migrate()
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='leaked'").fetchone() is None


def test_trigger_semicolons_comments_and_unterminated_final_statement(tmp_path):
    db = tmp_path / 'trigger.db'
    sql = """
    -- semicolon in comment ;
    CREATE TABLE items(value TEXT);
    CREATE TABLE copies(value TEXT);
    CREATE TRIGGER copied AFTER INSERT ON items BEGIN
      INSERT INTO copies VALUES(new.value);
      INSERT INTO copies VALUES('literal;semicolon');
    END;
    INSERT INTO items VALUES('ok')
    """
    assert Migrator(db, [Migration(1, 'trigger', sql)]).migrate() == [1]
    with sqlite3.connect(db) as connection:
        assert connection.execute('SELECT value FROM copies').fetchall() == [('ok',), ('literal;semicolon',)]


@pytest.mark.parametrize('sql', ['BEGIN;', 'ROLLBACK;', 'SAVEPOINT bypass;', 'PRAGMA journal_mode=OFF;', 'DELETE FROM schema_migrations;'])
def test_script_cannot_control_transaction_or_ledger(tmp_path, sql):
    db = tmp_path / 'protected.db'
    with pytest.raises(sqlite3.Error):
        Migrator(db, [Migration(1, 'bypass', sql)]).migrate()
    with sqlite3.connect(db) as connection:
        assert connection.execute('SELECT count(*) FROM schema_migrations').fetchone()[0] == 0
