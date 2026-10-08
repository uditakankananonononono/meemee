"""Malformed URL hosts return an explicit deny rather than crashing policy evaluation."""

import pytest

from meemee.policy import PolicyEngine
from meemee.types import Risk


@pytest.mark.parametrize('url', ['https://[broken/', 'https://example.com\uff0fadmin'])
def test_policy_denies_unparseable_url(url):
    result = PolicyEngine({'deny_hosts': ['blocked.example']}).evaluate('browser.navigate', {'url': url}, Risk.READ)
    assert result.allowed is False
    assert result.require_approval is False
    assert result.reason == 'URL is invalid'
