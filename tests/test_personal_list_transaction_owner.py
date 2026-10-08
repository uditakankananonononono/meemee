"""Personal list may reuse but must never commit a caller-owned transaction."""

from meemee.personal_model import PersonalItemInput, PersonalModelStore


def test_personal_list_preserves_callers_pending_transaction(tmp_path):
    store = PersonalModelStore(tmp_path / 'personal.db')
    item = store.upsert('owner', PersonalItemInput(kind='goal', title='goal', value='value', source_id='user', source_record_id='record'))
    store.db.execute('BEGIN IMMEDIATE')
    store.db.execute('UPDATE personal_items SET confidence=.123 WHERE id=?', (item['id'],))
    assert store.db.in_transaction
    assert store.list('owner', include_history=True)[0]['confidence'] == .123
    assert store.db.in_transaction
    store.db.rollback()
    assert store.get('owner', item['id'])['confidence'] == 1.0
