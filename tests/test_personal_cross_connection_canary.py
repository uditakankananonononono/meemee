"""Independent SQLite writers must atomically merge identical claims."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from meemee.personal_model import PersonalItemInput, PersonalModelStore


class SlowConnection:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.__enter__()
        return self

    def __exit__(self, *args):
        return self.db.__exit__(*args)

    def execute(self, sql, args=()):
        result = self.db.execute(sql, args)
        if sql.startswith('SELECT * FROM personal_items WHERE owner_id='):
            time.sleep(0.04)
        return result


def test_independent_personal_writers_merge_one_claim(tmp_path):
    stores = [PersonalModelStore(tmp_path / 'p.db'), PersonalModelStore(tmp_path / 'p.db')]
    for store in stores:
        store.db = SlowConnection(store.db)
    ready = threading.Barrier(2)

    def write(i):
        ready.wait()
        return stores[i].upsert('o', PersonalItemInput(kind='preference', title='drink', value='tea', source_id='fixture', source_record_id=str(i)))

    with ThreadPoolExecutor(2) as pool:
        rows = list(pool.map(write, range(2)))
    assert rows[0]['id'] == rows[1]['id']
    assert len(stores[0].get('o', rows[0]['id'])['evidence']) == 2
