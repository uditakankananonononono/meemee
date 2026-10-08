"""Policy should deny invalid JSON arguments instead of approving nonfinite values."""

import pytest

from meemee.policy import PolicyEngine
from meemee.types import Risk


@pytest.mark.parametrize('value', [float('nan'), float('inf'), float('-inf')])
def test_policy_denies_nonfinite_arguments(value):
    decision = PolicyEngine().evaluate('tool', {'value': value}, Risk.READ)
    assert decision.allowed is False
    assert decision.require_approval is False
    assert decision.reason == 'arguments must contain finite JSON numbers'
