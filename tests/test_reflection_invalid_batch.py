"""Model validity errors are caught before any batch claim is published."""

import asyncio
import json

import pytest
from test_personal_context_backends import stores  # noqa: F401

from meemee.context import ContextRecord
from meemee.reflection import PersonalModelReflector


def test_invalid_later_claim_does_not_publish_earlier_claim(stores):  # noqa: F811
    personal, context = stores()
    context.register_source('owner', 'source', 'test', {})
    context.ingest(ContextRecord('owner', 'source', 'event', 'event', 'preferences', 'likes tea and swimming', '2026-10-08', {}))
    claim = {'kind': 'preference', 'title': 'drink', 'value': 'tea', 'confidence': 0.8,
             'source_id': 'source', 'source_record_id': 'event'}

    class Model:
        async def chat(self, *args, **kwargs):
            return json.dumps({'claims': [claim, {**claim, 'title': 'exercise', 'value': 'swimming',
                                                 'valid_until': 'invalid-timestamp'}]})

    with pytest.raises(ValueError):
        asyncio.run(PersonalModelReflector(context, personal, Model()).reflect('owner'))
    assert personal.list('owner', include_history=True) == []
