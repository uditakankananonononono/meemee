"""Retention must not leave webhook attempt payloads behind removed deliveries."""
import sqlite3
from datetime import datetime, timezone

from meemee.retention import RetentionManager


def test_retention_deletes_attempts_with_terminal_deliveries(tmp_path):
    db = sqlite3.connect(tmp_path / 'webhooks.sqlite3')
    db.executescript("""CREATE TABLE webhook_deliveries(id TEXT,status TEXT,created_at TEXT);
        CREATE TABLE webhook_attempts(delivery_id TEXT,detail TEXT);
        INSERT INTO webhook_deliveries VALUES('old','delivered','2020-01-01T00:00:00+00:00');
        INSERT INTO webhook_deliveries VALUES('new','delivered','2026-10-07T00:00:00+00:00');
        INSERT INTO webhook_attempts VALUES('old','old payload');
        INSERT INTO webhook_attempts VALUES('new','new payload');""")
    db.close()
    RetentionManager(tmp_path).run(now=datetime(2026,10,7,tzinfo=timezone.utc))
    db = sqlite3.connect(tmp_path / 'webhooks.sqlite3')
    assert db.execute('SELECT delivery_id FROM webhook_attempts').fetchall() == [('new',)]
