"""Independent SQLite connections must publish one linear audit chain."""
import threading
from concurrent.futures import ThreadPoolExecutor

from meemee.audit import AuditLog


def test_independent_audit_connections_cannot_fork_chain(tmp_path):
    logs = [AuditLog(tmp_path / 'shared.db') for _ in range(2)]
    barrier = threading.Barrier(2)
    # Original code reads the predecessor before acquiring the write lock.
    # If fixed code holds BEGIN IMMEDIATE, the first writer times out this
    # test-only barrier and commits; the second sees the committed predecessor.
    def sync_before_read(statement):
        if statement.startswith('SELECT entry_hash FROM audit_log'):
            try:
                barrier.wait(timeout=0.2)
            except threading.BrokenBarrierError:
                pass
    for log in logs:
        log.db.set_trace_callback(sync_before_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda log: log.append('actor', 'test', 'resource', 'ok'), logs))
    assert logs[0].verify() == (True, None)
    assert len(logs[0].list()) == 2
    for log in logs:
        log.db.close()
