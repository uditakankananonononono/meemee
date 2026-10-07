"""Generated claims must not overwrite explicit owner corrections."""
import json

from meemee.context import ContextRecord, ContextStore
from meemee.personal_model import PersonalItemInput, PersonalModelStore
from meemee.reflection import PersonalModelReflector


async def test_generated_conflict_cannot_replace_owner_correction(tmp_path):
    context = ContextStore(tmp_path / 'c.db')
    context.register_source('o', 's', 'fixture', {})
    context.ingest(ContextRecord('o','s','r','document','preference','likes tea','2026-01-01T00:00:00Z',{}))
    personal = PersonalModelStore(tmp_path / 'p.db')
    row = personal.upsert('o', PersonalItemInput(kind='preference', title='drink', value='tea', source_id='s', source_record_id='r'))
    corrected = personal.correct('o', row['id'], 'coffee')

    class Model:
        async def chat(self, messages, **kwargs):
            return json.dumps({'claims':[{'kind':'preference','title':'drink','value':'tea','confidence':0.9,'source_id':'s','source_record_id':'r'}]})

    result = await PersonalModelReflector(context, personal, Model()).reflect('o')
    assert personal.list('o')[0]['id'] == corrected['id']
    assert personal.list('o')[0]['value'] == 'coffee'
    assert result['rejected'] == 1 and result['accepted'] == 0
