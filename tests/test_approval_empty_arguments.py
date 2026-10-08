"""Malformed argument containers do not become an empty authorized JSON object."""

import pytest

from meemee.approvals import constraints_match


@pytest.mark.parametrize('arguments', [[], '', 0, False])
def test_empty_constraint_rejects_nonobject_arguments(arguments):
    assert constraints_match({}, arguments) is False


def test_empty_constraint_accepts_missing_or_empty_object():
    assert constraints_match({}, None) is True
    assert constraints_match({}, {}) is True
