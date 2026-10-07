"""The local command producer must not issue invalid verifier envelopes."""
import pytest

from meemee.device_protocol import make_command


@pytest.mark.parametrize('kwargs', [
    {'device_id': 123}, {'capability': ['echo']}, {'arguments': []}, {'issued_at': True},
])
def test_make_command_rejects_invalid_field(kwargs):
    values = {'device_id': 'd', 'capability': 'echo', 'arguments': {}, 'secret': b'fixture'}
    values.update(kwargs)
    with pytest.raises(ValueError, match='command'):
        make_command(**values)
