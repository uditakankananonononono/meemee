"""A later store error must roll back the reflection batch, not only that claim."""

import asyncio
import json

import pytest
from test_personal_context_backends import stores  # noqa: F401

from meemee.context import ContextRecord
from meemee.reflection import PersonalModelReflector


def test_later_reflection_write_failure_rolls_back_batch(stores):  # noqa: F811
    personal, context = stores()
    context.register_source('owner', 'source', 'test', {})
    context.ingest(ContextRecord('owner', 'source', 'event', 'event', 'preferences', 'likes tea and swim', '2026-10-08', {}))
    if hasattr(personal, 'lock'):
        personal.db.execute("""CREATE TRIGGER reject_exercise BEFORE INSERT ON personal_items
            WHEN new.title='exercise' BEGIN SELECT RAISE(ABORT, 'injected store failure'); END""")
    else:
        with personal.db.transaction() as c:
            c.execute("ALTER TABLE meemee_personal_items ADD CONSTRAINT reject_exercise CHECK (title <> 'exercise')")
    claim = {'kind': 'preference', 'title': 'drink', 'value': 'tea', 'confidence': 0.8,
             'source_id': 'source', 'source_record_id': 'event'}

    class Model:
        async def chat(self, *args, **kwargs):
            return json.dumps({'claims': [claim, {**claim, 'title': 'exercise', 'value': 'swim'}]})

    with pytest.raises(Exception, match='injected store failure|reject_exercise'):
        asyncio.run(PersonalModelReflector(context, personal, Model()).reflect('owner'))
    assert personal.list('owner', include_history=True) == []
