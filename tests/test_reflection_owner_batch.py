"""Caller-supplied reflection batches retain context owner isolation."""

import asyncio
import json

import pytest
from test_personal_context_backends import stores  # noqa: F401

from meemee.context import ContextRecord
from meemee.reflection import PersonalModelReflector


class Model:
    def __init__(self):
        self.calls = 0

    async def chat(self, *args, **kwargs):
        self.calls += 1
        return json.dumps({'claims': [{'kind': 'preference', 'title': 'drink', 'value': 'tea',
                                      'confidence': 0.8, 'source_id': 'source', 'source_record_id': 'event'}]})


def test_reflection_refuses_another_owners_batch_before_model(stores):  # noqa: F811
    personal, context = stores()
    context.register_source('alice', 'source', 'test', {})
    context.ingest(ContextRecord('alice', 'source', 'event', 'event', 'drink', 'likes tea', '2026-10-08', {}))
    model = Model()
    reflector = PersonalModelReflector(context, personal, model)
    with pytest.raises(ValueError, match='owner'):
        asyncio.run(reflector.reflect_records('bob', context.recent('alice')))
    assert model.calls == 0
    assert personal.list('bob') == []
