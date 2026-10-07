"""Execute PostgreSQL adapter methods against stateful rows, not a live PG server."""
from contextlib import contextmanager

from meemee.personal_model import PersonalItemInput
from meemee_persist_pg.personal import PersonalModelStore


class Result:
    def __init__(self, rows):
        self.rows = rows
    def fetchall(self):
        return self.rows
    def fetchone(self):
        return self.rows[0] if self.rows else None


class ContractDB:
    def __init__(self):
        self.row = {'id':'item', 'owner_id':'owner', 'kind':'project', 'title':'trip',
            'value':'Travel', 'confidence':0.5, 'status':'active',
            'valid_from':None, 'valid_until':'2020-01-01T00:00:00+00:00'}
    @contextmanager
    def transaction(self, *, isolation=None):
        yield self
    def execute(self, sql, params=()):
        if sql.startswith('SELECT pg_advisory'):
            return Result([])
        if sql.startswith('SELECT id FROM'):
            return Result([{'id':'item'}])
        if sql.startswith('SELECT * FROM'):
            return Result([self.row.copy()])
        if 'UPDATE meemee_personal_items SET confidence=' in sql:
            if 'valid_from=' in sql and 'valid_until=' in sql:
                self.row.update(confidence=params[0], valid_from=params[1], valid_until=params[2])
            else:
                self.row['confidence'] = params[0]
        if sql.startswith('SELECT id,valid_until FROM'):
            return Result([self.row.copy()])
        if "UPDATE meemee_personal_items SET status='superseded'" in sql:
            # Mirror existing adapter's text comparison, not PostgreSQL TIMESTAMPTZ.
            if 'valid_until<=%s' not in sql or self.row['valid_until'] <= params[-1]:
                self.row['status'] = 'superseded'
                result = Result([])
                result.rowcount = 1
                return result
            result = Result([])
            result.rowcount = 0
            return result
        return Result([])


def test_pg_active_context_filters_expired_rows_without_claiming_live_sql():
    db = ContractDB()
    store = PersonalModelStore(db)
    assert store.list('owner') == []
    assert len(store.list('owner', include_history=True)) == 1


def test_pg_same_value_refresh_updates_temporal_contract():
    db = ContractDB()
    row = PersonalModelStore(db).upsert('owner', PersonalItemInput(kind='project', title='trip',
        value='Travel', source_id='calendar', source_record_id='e2', valid_until='2099-01-01T00:00:00Z'))
    assert row['valid_until'] == '2099-01-01T00:00:00+00:00'


def test_pg_expiry_compares_offsets_not_lexical_text():
    db = ContractDB()
    db.row['valid_until'] = '2026-10-07T16:00:00+05:30'
    assert PersonalModelStore(db).expire('owner', at='2026-10-07T11:00:00Z') == 1
    assert db.row['status'] == 'superseded'
