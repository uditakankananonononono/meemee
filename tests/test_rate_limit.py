from pathlib import Path

from meemee.rate_limit import SQLiteRateLimiter


def test_shared_limiter_counts_across_instances(tmp_path: Path):
    path = tmp_path / "limits.db"
    first = SQLiteRateLimiter(path, limit=2, window_seconds=60)
    second = SQLiteRateLimiter(path, limit=2, window_seconds=60)
    assert first.hit("user", now=120) == (True, 1, 180)
    assert second.hit("user", now=121) == (True, 0, 180)
    assert first.hit("user", now=122) == (False, 0, 180)


def test_limiter_isolates_identity_and_resets(tmp_path: Path):
    limiter = SQLiteRateLimiter(tmp_path / "limits.db", limit=1, window_seconds=10)
    assert limiter.hit("a", now=1)[0]
    assert not limiter.hit("a", now=2)[0]
    assert limiter.hit("b", now=2)[0]
    assert limiter.hit("a", now=11)[0]


def test_limiter_cleanup(tmp_path: Path):
    limiter = SQLiteRateLimiter(tmp_path / "limits.db", limit=1, window_seconds=10)
    limiter.hit("a", now=1); limiter.hit("a", now=21)
    assert limiter.cleanup(now=31) == 1
