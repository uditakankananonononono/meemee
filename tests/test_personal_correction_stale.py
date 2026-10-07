"""Correcting a stale item must not overwrite the replacement current claim."""

from test_personal_context_backends import stores  # noqa: F401

from meemee.personal_model import PersonalItemInput


def test_stale_correction_does_not_replace_new_current(stores, monkeypatch):  # noqa: F811
    personal, _ = stores()

    def value(text):
        return PersonalItemInput(kind='preference', title='drink', value=text,
                                 source_id='chat', source_record_id=text)

    old = personal.upsert('owner', value('tea'))
    original = personal.get
    replaced = []

    def raced_get(owner, ident):
        row = original(owner, ident)
        if ident == old['id'] and not replaced:
            replaced.append(True)
            personal.upsert('owner', value('coffee'))
        return row

    monkeypatch.setattr(personal, 'get', raced_get)
    assert personal.correct('owner', old['id'], 'juice') is None
    assert [r['value'] for r in personal.list('owner')] == ['coffee']
    assert len(personal.list('owner', include_history=True)) == 2
