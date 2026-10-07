"""Valid signature does not make malformed command fields executable."""
import pytest

from meemee.device_protocol import make_command, sign, verify_command


@pytest.mark.parametrize(('field', 'value'), [
    ('version', True), ('command_id', ''), ('device_id', 123),
    ('capability', ['echo']), ('issued_at', 1000.9), ('nonce', {}),
])
def test_signed_malformed_field_rejected_before_execution(field, value):
    envelope = make_command('d', 'echo', {}, b'fixture', issued_at=1000)
    envelope[field] = value
    envelope['signature'] = sign(b'fixture', envelope)
    with pytest.raises(ValueError, match='envelope'):
        verify_command(envelope, b'fixture', now=1000)
