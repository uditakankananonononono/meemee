"""Email-verification and password-reset challenges: one contract, SQLite and PostgreSQL."""
import threading

from test_token_audit_backends import persistence  # noqa: F401 - shared two-backend fixture

from meemee.email_verification import EmailVerificationStore as SQLiteStore
from meemee_persist_pg.interfaces import EmailVerificationStoreInterface


def _account(persistence, email="ev@example.com"):  # noqa: F811
    account, _ = persistence.tokens.create_account(email, "correct horse battery", "EV")
    return account["id"]


def test_store_matches_interface(persistence):  # noqa: F811
    assert isinstance(persistence.email_verifications, EmailVerificationStoreInterface)
    assert persistence.email_verifications.ping()


def test_verification_is_one_time_and_reissue_replaces_the_old_link(persistence):  # noqa: F811
    store, acct = persistence.email_verifications, _account(persistence)
    assert store.status(acct) is False
    first = store.issue(acct)
    second = store.issue(acct)
    assert not store.verify(acct, first)  # replaced
    assert not store.verify(acct, "wrong-token-that-is-long-enough")
    assert store.verify(acct, second) and store.status(acct) is True
    assert not store.verify(acct, second)  # one-time
    third = store.issue(acct)  # re-issuing resets verified state, like SQLite
    assert store.status(acct) is False and store.verify(acct, third)


def test_expired_challenges_fail(persistence):  # noqa: F811
    store, acct = persistence.email_verifications, _account(persistence)
    raw = store.issue(acct, ttl_minutes=0)
    assert not store.verify(acct, raw) and store.status(acct) is False
    reset = store.issue_password_reset(acct, ttl_minutes=-1)
    assert not store.consume_password_reset(acct, reset)


def test_password_reset_is_one_time_and_separate_from_verification(persistence):  # noqa: F811
    store, acct = persistence.email_verifications, _account(persistence)
    verify = store.issue(acct)
    reset = store.issue_password_reset(acct)
    assert not store.consume_password_reset(acct, verify)
    assert not store.verify(acct, reset)
    assert store.consume_password_reset(acct, reset)
    assert not store.consume_password_reset(acct, reset)
    assert store.verify(acct, verify)
    assert not store.verify("acct_missing", verify)


def test_concurrent_consumers_of_one_link_get_exactly_one_success(persistence):  # noqa: F811
    store, acct = persistence.email_verifications, _account(persistence)
    raw = store.issue_password_reset(acct)
    results, barrier = [], threading.Barrier(8)

    def consume():
        barrier.wait()
        results.append(store.consume_password_reset(acct, raw))

    threads = [threading.Thread(target=consume) for _ in range(8)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert results.count(True) == 1


def test_raw_tokens_are_never_stored(persistence, tmp_path):  # noqa: F811
    store, acct = persistence.email_verifications, _account(persistence)
    raw = store.issue(acct)
    if persistence.backend == "sqlite":
        assert raw.encode() not in (tmp_path / "email-verifications.sqlite3").read_bytes()
        return
    import hashlib

    import psycopg
    with psycopg.connect(persistence.dsn) as c:
        digest = c.execute("SELECT token_digest FROM meemee_email_verifications WHERE account_id=%s", (acct,)).fetchone()[0]
    assert bytes(digest) == hashlib.sha256(raw.encode()).digest()


def test_sqlite_store_ping(tmp_path):
    assert SQLiteStore(tmp_path / "e.sqlite3").ping()
