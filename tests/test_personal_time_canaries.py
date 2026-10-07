"""Personal context must honor timezone-aware validity, not string ordering."""
import pytest
from pydantic import ValidationError

from meemee.personal_model import PersonalItemInput, PersonalModelStore


def entry(**times):
    return PersonalItemInput(kind='project', title='trip', value='Travel',
        source_id='calendar', source_record_id='e1', **times)


def test_expiry_compares_instants_across_offsets(tmp_path):
    store = PersonalModelStore(tmp_path / 'model.db')
    row = store.upsert('owner', entry(valid_until='2026-10-07T16:00:00+05:30'))
    assert store.expire('owner', at='2026-10-07T11:00:00+00:00') == 1
    assert store.get('owner', row['id'])['status'] == 'superseded'


def test_active_list_never_supplies_expired_claims(tmp_path):
    store = PersonalModelStore(tmp_path / 'model.db')
    store.upsert('owner', entry(valid_until='2020-01-01T00:00:00Z'))
    assert store.list('owner') == []
    assert len(store.list('owner', include_history=True)) == 1


@pytest.mark.parametrize('times', [
    {'valid_until':'not-a-date'},
    {'valid_from':'2026-10-07T12:00:00'},
    {'valid_from':'2026-10-08T00:00:00Z','valid_until':'2026-10-07T00:00:00Z'},
])
def test_invalid_validity_rejected_before_persistence(times):
    with pytest.raises(ValidationError):
        entry(**times)


def test_same_value_refresh_updates_validity_window(tmp_path):
    store = PersonalModelStore(tmp_path / 'model.db')
    first = store.upsert('owner', entry(valid_until='2020-01-01T00:00:00Z'))
    refreshed = store.upsert('owner', entry(valid_until='2099-01-01T00:00:00Z'))
    assert refreshed['id'] == first['id']
    assert refreshed['valid_until'] == '2099-01-01T00:00:00+00:00'
    assert len(store.list('owner')) == 1
