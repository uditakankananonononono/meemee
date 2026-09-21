from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from meemee.audit import AuditLog
from meemee.auth import TokenStore


def test_token_authentication_is_thread_safe(tmp_path: Path):
    store = TokenStore(tmp_path / "tokens.db")
    _, token = store.create("load", {"jobs:read"})
    with ThreadPoolExecutor(max_workers=32) as pool:
        results = list(pool.map(lambda _: store.authenticate(token), range(2000)))
    assert all(result and result.name == "load" for result in results)


def test_audit_chain_is_thread_safe_under_parallel_append_and_read(tmp_path: Path):
    audit = AuditLog(tmp_path / "audit.db")
    def work(index: int):
        audit.append("load", "test", str(index), "success")
        if index % 10 == 0:
            assert audit.verify()[0]
            audit.list(max(0, index-20), 20)
    with ThreadPoolExecutor(max_workers=32) as pool:
        list(pool.map(work, range(1000)))
    assert audit.verify() == (True, None)
    assert len(audit.list(limit=500)) == 500
