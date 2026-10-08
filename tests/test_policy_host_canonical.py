"""Denied DNS hosts cannot bypass policy with their absolute trailing-dot spelling."""

import pytest

from meemee.policy import PolicyEngine
from meemee.types import Risk


@pytest.mark.parametrize('host', ['blocked.example.', 'BLOCKED.EXAMPLE.'])
def test_denied_dns_host_rejects_absolute_spelling(host):
    policy = PolicyEngine({'deny_hosts': ['blocked.example']})
    result = policy.evaluate('browser.navigate', {'url': 'https://' + host + '/private'}, Risk.READ)
    assert result.allowed is False
    assert result.reason == 'URL host is denied'
