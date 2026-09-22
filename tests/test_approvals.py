from datetime import datetime, timedelta, timezone
from pathlib import Path

from meemee.approvals import ApprovalStore


def test_approval_grant_expire_revoke(tmp_path: Path):
    store = ApprovalStore(tmp_path / "a.db")
    store.grant("u", "git.commit", "admin")
    assert store.allows("u", "git.commit")
    assert not store.allows("other", "git.commit")
    assert store.revoke("u", "git.commit")
    assert not store.allows("u", "git.commit")


def test_approval_expiry_and_regrant(tmp_path: Path):
    store = ApprovalStore(tmp_path / "a.db")
    past = (datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
    store.grant("u", "workspace.write_file", "admin", past)
    assert not store.allows("u", "workspace.write_file")
    store.grant("u", "workspace.write_file", "admin")
    assert store.allows("u", "workspace.write_file")
    assert store.list("u")[0]["revoked_at"] is None


def test_active_count_excludes_revoked_and_expired(tmp_path: Path):
    store=ApprovalStore(tmp_path/"a.db")
    store.grant("u","a","admin")
    store.grant("u","expired","admin",(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat())
    store.grant("u","revoked","admin"); store.revoke("u","revoked")
    assert store.active_count("u")==1


def test_argument_scoped_approval_matches_exact_constraint_subset(tmp_path: Path):
    store=ApprovalStore(tmp_path/"a.db")
    store.grant("u","github.push_branch","admin",argument_constraints={"owner":"acme","repository":"safe"})
    assert store.allows("u","github.push_branch",arguments={"owner":"acme","repository":"safe","branch":"feature"})
    assert not store.allows("u","github.push_branch",arguments={"owner":"evil","repository":"safe"})
    assert not store.allows("u","github.push_branch")
    assert store.list("u")[0]["argument_constraints"] == {"owner":"acme","repository":"safe"}


def test_regrant_can_narrow_then_remove_constraints(tmp_path: Path):
    store=ApprovalStore(tmp_path/"a.db")
    store.grant("u","shell.command","admin",argument_constraints={"command":"pytest"})
    assert not store.allows("u","shell.command",arguments={"command":"git"})
    store.grant("u","shell.command","admin")
    assert store.allows("u","shell.command",arguments={"command":"git"})
