"""Malformed non-ASCII signatures must reject, not crash authentication."""
import pytest

from meemee.device_protocol import make_command, verify_command
from meemee.webhook_verify import verify_signature


def test_webhook_nonascii_signature_returns_false():
    assert verify_signature('fixture', '1000', b'{}', 'sha256=\u2603', now=1000) is False


def test_device_nonascii_signature_is_validation_error():
    envelope = make_command('d', 'echo', {}, b'fixture', issued_at=1000)
    envelope['signature'] = '\u2603'
    with pytest.raises(ValueError, match='signature'):
        verify_command(envelope, b'fixture', now=1000)
