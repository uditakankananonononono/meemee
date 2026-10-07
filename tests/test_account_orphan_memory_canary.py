"""Account deletion must not depend on retained job/run reports."""
from test_account_deletion import build_targets

from meemee.account_deletion import AccountPurger, DeletionLedger


def test_account_purge_removes_orphan_memories_only_for_owner(tmp_path, monkeypatch):
    targets = build_targets(tmp_path, monkeypatch)
    targets.memory.add('lost-run', 'goal', 'ownerorphan private fact', owner_id='alice')
    targets.memory.add('lost-run', 'goal', 'otherorphan private fact', owner_id='bob')
    assert targets.runs.run_ids('alice') == []
    assert targets.jobs.run_ids_for_principal('alice') == []
    result = AccountPurger(targets, DeletionLedger(tmp_path / 'd.db')).purge('alice', 'alice')
    assert result['status'] == 'completed'
    assert targets.memory.recent(owner_id='alice') == []
    assert targets.memory.search('ownerorphan', owner_id='alice') == []
    assert len(targets.memory.recent(owner_id='bob')) == 1
    assert result['deleted']['memories'] == 1


def test_owner_purge_cleans_embeddings_and_preserves_other_owner(tmp_path):
    from meemee.memory import MemoryStore

    memory = MemoryStore(tmp_path / 'm.db')
    removed = memory.add('missing', 'goal', 'ownerembedding orphan', owner_id='alice')
    kept = memory.add('missing', 'goal', 'otherembedding orphan', owner_id='bob')
    assert memory.purge_owner('alice') == 1
    assert memory.connection.execute('SELECT memory_id FROM memory_embeddings').fetchall()[0][0] == kept
    assert memory.connection.execute('SELECT memory_id FROM memory_embeddings WHERE memory_id=?', (removed,)).fetchall() == []
    assert memory.search('ownerembedding', owner_id='alice') == []
