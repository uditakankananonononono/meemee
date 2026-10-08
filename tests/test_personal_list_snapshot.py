"""Personal history must not yield null entries across a purge between list reads."""

from pathlib import Path

from test_token_audit_backends import persistence  # noqa: F401

from meemee.personal_model import PersonalItemInput


def test_personal_history_list_remains_coherent(persistence, monkeypatch):  # noqa: F811
    store = persistence.personal_model
    row = store.upsert('owner', PersonalItemInput(kind='preference', title='drink', value='tea', source_id='user', source_record_id='1'))
    original = store.get
    def purged_before_read(owner, ident):
        store.purge_owner(owner)
        return original(owner, ident)
    monkeypatch.setattr(store, 'get', purged_before_read)
    result = store.list('owner', include_history=True)
    assert all(isinstance(item, dict) for item in result)
    assert [item['id'] for item in result] == [row['id']]


def test_personal_list_stays_on_snapshot_when_independent_writer_replaces_items(persistence, tmp_path, monkeypatch):  # noqa: F811
    from meemee.personal_model import PersonalModelStore

    reader = persistence.personal_model
    if persistence.backend == 'sqlite':
        path = reader.db.execute('PRAGMA database_list').fetchone()[2]
        writer = PersonalModelStore(Path(path))
    else:
        writer = type(reader)(reader.db)
    def value(title, text):
        return PersonalItemInput(kind='preference', title=title, value=text, source_id='source', source_record_id=text)
    originals = reader.upsert_batch('owner', [value('one', 'old'), value('two', 'old')])
    changed = []
    method = '_get_locked' if persistence.backend == 'sqlite' else '_get_on'
    original = getattr(reader, method)
    def changed_after_ids(*args):
        if not changed:
            changed.append(True)
            writer.upsert_batch('owner', [value('one', 'new'), value('two', 'new')])
        return original(*args)
    monkeypatch.setattr(reader, method, changed_after_ids)
    result = reader.list('owner', include_history=True)
    assert {item['id'] for item in result} == {item['id'] for item in originals}
    assert {item['status'] for item in result} == {'active'}
    assert {item['value'] for item in result} == {'old'}
    assert {entry['observed_value'] for item in result for entry in item['evidence']} == {'old'}
    monkeypatch.setattr(reader, method, original)
    assert {item['value'] for item in writer.list('owner')} == {'new'}
