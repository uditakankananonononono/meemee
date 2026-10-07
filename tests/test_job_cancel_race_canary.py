"""Cancellation must not overwrite independently published completion."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from meemee.jobs import JobStore


def test_cancel_completion_race_serializes_status(tmp_path):
    stores = [JobStore(tmp_path / 'j.db'), JobStore(tmp_path / 'j.db')]
    ident = stores[0].enqueue('fixture', principal='owner')
    stores[0].claim()
    reading = threading.Event()

    def paused_read():
        reading.set()
        time.sleep(0.15)
        return 1

    stores[0].db.create_function('pause_read', 0, paused_read)
    stores[0].db.execute('CREATE VIEW cancel_race AS SELECT id,status,pause_read() AS pause FROM jobs')
    real = stores[0].db

    class DelayedConnection:
        def execute(self, sql, *args):
            if sql == 'SELECT status FROM jobs WHERE id=?':
                sql = 'SELECT status FROM cancel_race WHERE id=? AND pause=1'
            return real.execute(sql, *args)
        def __enter__(self):
            return real.__enter__()
        def __exit__(self, *args):
            return real.__exit__(*args)
        def __getattr__(self, name):
            return getattr(real, name)

    stores[0].db = DelayedConnection()
    with ThreadPoolExecutor(max_workers=2) as pool:
        cancel = pool.submit(stores[0].request_cancel, ident)
        assert reading.wait(2)
        finished = pool.submit(stores[1].finish, ident, {'fixture': True})
        cancellation = cancel.result(timeout=3)
        try:
            finished.result(timeout=3)
        except ValueError:
            pass  # Correct ordering: cancel wins, finish refuses.
    row = stores[1].get(ident)
    # Completion cannot be published, then silently changed to cancel_requested.
    kinds = [event['kind'] for event in stores[1].events(ident)]
    assert not ('done' in kinds and row['status'] == 'cancel_requested')
    assert cancellation in {'cancel_requested', 'done'}
