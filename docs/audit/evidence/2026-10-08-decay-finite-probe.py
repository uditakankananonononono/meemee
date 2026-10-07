"""A nonfinite decay factor must not destroy durable claim confidence."""

import pytest
from test_token_audit_backends import persistence  # noqa: F401

from meemee.personal_model import PersonalItemInput


def test_decay_rejects_nan_without_changing_claim(persistence):  # noqa: F811
    store = persistence.personal_model
    item = store.upsert('owner', PersonalItemInput(kind='preference', title='drink', value='tea', confidence=0.8, source_id='user', source_record_id='1'))
    with pytest.raises(ValueError, match='factor'):
        store.decay('owner', '2999-01-01T00:00:00+00:00', float('nan'))
    assert store.get('owner', item['id'])['confidence'] == 0.8
