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
