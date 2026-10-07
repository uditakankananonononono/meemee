"""Personal evidence cannot be blank after store normalization."""

import pytest

from meemee.personal_model import PersonalItemInput


@pytest.mark.parametrize('field', ['title', 'value', 'source_id', 'source_record_id'])
def test_personal_input_rejects_blank_required_text(field):
    data = {'kind': 'preference', 'title': 'drink', 'value': 'tea', 'source_id': 'chat', 'source_record_id': 'record'}
    data[field] = ' \t\n '
    with pytest.raises(ValueError):
        PersonalItemInput(**data)
