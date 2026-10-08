"""Personal history must not yield null entries across a purge between list reads."""

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
