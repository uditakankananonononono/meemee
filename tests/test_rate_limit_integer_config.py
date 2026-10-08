"""Fixed-window configuration preserves integer request and second counts."""

import pytest
from test_token_audit_backends import persistence  # noqa: F401

from meemee.rate_limit import SQLiteRateLimiter
from meemee_persist_pg.rate_limit import PostgreSQLRateLimiter


@pytest.mark.parametrize('field', ['limit', 'window_seconds'])
@pytest.mark.parametrize('value', [True, 1.5])
def test_rate_configuration_rejects_non_integer_counts(persistence, tmp_path, field, value):  # noqa: F811
    cls = SQLiteRateLimiter if persistence.backend == 'sqlite' else PostgreSQLRateLimiter
    target = tmp_path / 'rate.db' if persistence.backend == 'sqlite' else persistence.idempotency.db
    with pytest.raises(ValueError, match='positive integers'):
        cls(target, **{field: value})
