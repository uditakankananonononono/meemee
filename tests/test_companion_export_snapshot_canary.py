"""A separate connection cannot tear a user's export across snapshots."""
from meemee.companion.models import FactInput, UserProfile
from meemee.companion.store import CompanionStore


class ConcurrentDelete:
    def __init__(self, db, writer):
        self.db = db
        self.writer = writer
        self.deleted = False

    def __enter__(self):
        self.db.__enter__()
        return self

    def __exit__(self, *args):
        return self.db.__exit__(*args)

    def execute(self, sql, *args):
        if 'SELECT * FROM companion_facts' in sql and not self.deleted:
            self.deleted = True
            self.writer.delete_user_data('owner')
        return self.db.execute(sql, *args)


def test_export_snapshot_survives_other_connection_deletion(tmp_path):
    path = tmp_path / 'companion.db'
    reader = CompanionStore(path)
    writer = CompanionStore(path)
    reader.upsert_user(UserProfile(user_id='owner', display_name='Owner'))
    reader.add_fact('owner', FactInput(text='retained fact'), source='fixture')
    conv = reader.start_conversation('owner', 'local')
    reader.add_message(conv['id'], 'user', 'retained message')
    reader.db = ConcurrentDelete(reader.db, writer)
    export = reader.export_user_data('owner')
    assert export['profile'] is not None
    assert len(export['facts']) == 1
    assert len(export['conversations']) == 1
    assert len(export['messages']) == 1
    assert writer.get_user('owner') is None
