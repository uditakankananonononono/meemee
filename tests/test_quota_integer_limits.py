"""Job-count budgets are positive integer counts, not rounded or boolean values."""

import pytest
from test_token_audit_backends import persistence  # noqa: F401


@pytest.mark.parametrize('value', [True, 1.5])
@pytest.mark.parametrize('operation', ['default', 'set'])
def test_quota_limits_require_exact_integer(persistence, tmp_path, value, operation):  # noqa: F811
    quota = persistence.quotas
    with pytest.raises(ValueError, match='integer'):
        if operation == 'default':
            target = tmp_path / 'invalid.db' if persistence.backend == 'sqlite' else quota.db
            type(quota)(target, default_daily_jobs=value)
        else:
            quota.set_limit('owner', value)
    assert quota.limit('owner') == 100
