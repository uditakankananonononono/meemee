"""Durable run reports must not contain numbers that break JSON responses."""

import pytest
from test_token_audit_backends import persistence  # noqa: F401

from meemee.types import RunReport


def test_run_rejects_nonfinite_tool_result_before_publication(persistence):  # noqa: F811
    report = RunReport(run_id='nonfinite', goal='check', final='done', steps_used=1, tool_results=[{'value': float('nan')}])
    with pytest.raises(ValueError, match='JSON'):
        persistence.runs.add('owner', report)
    assert persistence.runs.get('owner', report.run_id) is None
