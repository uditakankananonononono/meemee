from pathlib import Path

from meemee.audit import AuditLog


def test_audit_chain_and_cursor(tmp_path: Path):
    log = AuditLog(tmp_path / "audit.db")
    first = log.append("u1", "job.create", "j1", "success", {"x": 1})
    log.append("u1", "job.cancel", "j1", "success")
    assert log.verify() == (True, None)
    assert len(log.list(first)) == 1


def test_audit_detects_tampering(tmp_path: Path):
    log = AuditLog(tmp_path / "audit.db")
    sequence = log.append("u1", "token.create", "t1", "success")
    with log.db:
        log.db.execute("UPDATE audit_log SET outcome='forged' WHERE sequence=?", (sequence,))
    assert log.verify() == (False, sequence)
