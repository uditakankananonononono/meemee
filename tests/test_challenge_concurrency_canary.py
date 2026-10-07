"""One-time challenges must serialize checks before marking consumed."""
import secrets
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from meemee.email_verification import EmailVerificationStore


@pytest.mark.parametrize('method', ['verify', 'consume_password_reset'])
def test_independent_challenge_consumers_have_one_success(tmp_path, monkeypatch, method):
    stores = [EmailVerificationStore(tmp_path / 'e.db'), EmailVerificationStore(tmp_path / 'e.db')]
    issue = stores[0].issue if method == 'verify' else stores[0].issue_password_reset
    token = issue('fixture')
    compare = secrets.compare_digest

    def synchronized(left, right):
        time.sleep(0.05)
        return compare(left, right)

    monkeypatch.setattr(secrets, 'compare_digest', synchronized)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda store: getattr(store, method)('fixture', token), stores))
    assert results.count(True) == 1
