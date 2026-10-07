"""Source validation and event publication share the same write transaction."""


from meemee.context import ContextRecord, ContextStore


def test_ingest_rechecks_source_inside_write_transaction(tmp_path, monkeypatch):
    store = ContextStore(tmp_path / 'context.sqlite3')
    other = ContextStore(tmp_path / 'context.sqlite3')
    store.register_source('owner', 'source', 'test', {})
    original = store.source

    def removed_after_read(owner, source):
        result = original(owner, source)
        if not store.db.in_transaction:
            with other.db:
                other.db.execute('DELETE FROM context_sources WHERE owner_id=? AND source_id=?', (owner, source))
        return result

    monkeypatch.setattr(store, 'source', removed_after_read)
    record = ContextRecord('owner', 'source', 'event', 'event', 'title', 'body', '2026-10-08', {})
    # Either source validation prevents deletion or ingestion rejects its disappearance.
    try:
        store.ingest(record)
    except ValueError:
        pass
    rows = store.db.execute('SELECT count(*) FROM context_records').fetchone()[0]
    assert rows == 0 or store.db.execute("SELECT 1 FROM context_sources WHERE owner_id='owner' AND source_id='source'").fetchone() is not None
