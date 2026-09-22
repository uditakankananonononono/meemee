import json

import pytest

from meemee.audit import AuditLog
from meemee.audit_anchor import create_anchor, verify_anchor

KEY = "a" * 32


def test_signed_external_anchor_round_trip_and_append_safety(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    log.append("u", "create", "one", "success")
    target = tmp_path / "external" / "anchor.json"
    anchor = create_anchor(log, target, KEY)
    assert anchor["sequence"] == 1
    assert verify_anchor(log, target, KEY)["status"] == "pass"
    log.append("u", "create", "two", "success")
    assert verify_anchor(log, target, KEY)["status"] == "pass"
    with log.db:
        log.db.execute("UPDATE audit_log SET outcome='forged' WHERE sequence=1")
    report = verify_anchor(log, target, KEY)
    assert report["status"] == "fail" and not report["chain_valid"]


def test_anchor_refuses_overwrite_short_key_invalid_chain_and_tampering(tmp_path):
    log = AuditLog(tmp_path / "audit.db"); log.append("u", "x", "r", "ok")
    target = tmp_path / "anchor.json"
    with pytest.raises(ValueError): create_anchor(log, target, "short")
    create_anchor(log, target, KEY)
    with pytest.raises(FileExistsError): create_anchor(log, target, KEY)
    doc=json.loads(target.read_text()); doc["sequence"]=99; target.write_text(json.dumps(doc))
    assert verify_anchor(log, target, KEY)["status"] == "fail"
    target.unlink()
    with log.db: log.db.execute("UPDATE audit_log SET outcome='forged'")
    with pytest.raises(ValueError): create_anchor(log, target, KEY)
