"""Protocol JSON must not sign nonfinite values or silently change key types."""
import pytest

from meemee.device_protocol import make_command


@pytest.mark.parametrize('arguments', [
    {'value': float('nan')}, {'value': float('inf')}, {'nested': [float('-inf')]},
    {1: 'numeric key'}, {'nested': {2: 'numeric key'}},
])
def test_command_producer_rejects_non_json_arguments(arguments):
    with pytest.raises(ValueError, match='JSON'):
        make_command('d', 'echo', arguments, b'fixture')
