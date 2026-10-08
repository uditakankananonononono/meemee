"""Policy byte budgets cannot silently round fractional values or accept booleans."""

import pytest

from meemee.policy import PolicyEngine
from meemee.types import Risk


@pytest.mark.parametrize('value', [True, 1.5])
def test_policy_byte_budget_rejects_non_integer_counts(value):
    with pytest.raises(ValueError, match='positive integer'):
        PolicyEngine({'max_argument_bytes': value})


@pytest.mark.parametrize('value', [1, 50, 1_000_000])
def test_policy_byte_budget_preserves_positive_integer_counts(value):
    policy = PolicyEngine({'max_argument_bytes': value})
    assert policy.max_argument_bytes == value
    assert policy.evaluate('tool', {'value': 'x' * value}, Risk.READ).allowed is False
