"""Loadcheck job counts cannot stop at the API page cap."""

from meemee.loadcheck import run_loadcheck


def test_loadcheck_counts_more_than_one_page_of_jobs():
    result = run_loadcheck(operations=2004, workers=8)
    assert result['counts']['job'] == 501
    assert result['stored_jobs'] == 501
    assert result['status'] == 'pass'
