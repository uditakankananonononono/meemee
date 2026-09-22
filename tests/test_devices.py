from pathlib import Path

import pytest

from meemee.devices import DeviceRegistry, DeviceSimulator


def test_pair_issue_simulate_audit_and_revoke(tmp_path: Path):
    registry = DeviceRegistry(tmp_path / "devices.db")
    pairing = registry.create_pairing("u")
    device = registry.pair(
        pairing["pairing_id"],
        pairing["code"],
        device_id="phone",
        name="Phone",
        capabilities={"math.add": {"args": ["a", "b"]}},
    )
    with pytest.raises(ValueError):
        registry.pair(
            pairing["pairing_id"],
            pairing["code"],
            device_id="other",
            name="Other",
            capabilities={"x": {}},
        )
    command = registry.issue("u", "phone", "math.add", {"a": 2, "b": 3})
    simulator = DeviceSimulator("phone", device["secret"], {"math.add": lambda a, b: a + b})
    assert simulator.execute(command, now=command["issued_at"]) == 5
    with pytest.raises(ValueError, match="replayed"):
        simulator.execute(command, now=command["issued_at"])
    registry.complete(command["command_id"], result={"value": 5})
    audit = registry.command(command["command_id"])
    assert audit["status"] == "completed" and audit["result"] == {"value": 5}
    assert registry.revoke("u", "phone")
    with pytest.raises(KeyError):
        registry.issue("u", "phone", "math.add", {})


def test_pairing_manifest_capability_and_owner_boundaries(tmp_path: Path):
    registry = DeviceRegistry(tmp_path / "devices.db")
    pairing = registry.create_pairing("u")
    with pytest.raises(ValueError):
        registry.pair(
            pairing["pairing_id"], "wrong", device_id="d", name="D", capabilities={"x": {}}
        )
    registry.pair(
        pairing["pairing_id"], pairing["code"], device_id="d", name="D", capabilities={"x": {}}
    )
    assert "secret_hex" not in registry.get("u", "d")
    with pytest.raises(PermissionError):
        registry.issue("u", "d", "y", {})
    with pytest.raises(KeyError):
        registry.issue("other", "d", "x", {})
    assert registry.update_manifest("u", "d", {"y": {}})["manifest"] == {"y": {}}
