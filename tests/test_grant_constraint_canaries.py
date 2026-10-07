"""Exact approval arguments must not collapse bool, number and malformed JSON."""
import pytest

from meemee.approvals import ApprovalStore, constraints_match


@pytest.mark.parametrize('expected,supplied', [(True,1),(1,True),({'enabled':True},{'enabled':1}),([False],[0])])
def test_exact_constraint_matching_preserves_json_types(expected,supplied):
    assert not constraints_match({'value':expected},{'value':supplied})


def test_malformed_grant_constraint_is_not_persisted(tmp_path):
    store = ApprovalStore(tmp_path / 'grants.db')
    with pytest.raises(ValueError):
        store.grant('owner','effect','owner',argument_constraints={'amount':float('nan')})
    assert store.list('owner') == []


def test_large_exact_integer_constraints_do_not_overflow():
    value = 10**1000
    assert constraints_match({'value':value},{'value':value})
    assert not constraints_match({'value':value},{'value':True})
