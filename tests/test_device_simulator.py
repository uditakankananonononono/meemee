from pathlib import Path

import pytest

from meemee.device_simulator import StatefulDeviceSimulator
from meemee.devices import DeviceRegistry


def test_stateful_simulator_executes_signed_commands_and_records_snapshots(tmp_path: Path):
    registry = DeviceRegistry(tmp_path / "d.db")
    pairing = registry.create_pairing("u")
    device = registry.pair(
        pairing["pairing_id"],
        pairing["code"],
        device_id="lamp",
        name="Lamp",
        capabilities={"light.set": {}},
    )

    def set_light(state, *, on):
        state["on"] = on
        return {"accepted": True}

    simulator = StatefulDeviceSimulator(
        "lamp", device["secret"], {"light.set": set_light}, {"on": False}
    )
    command = registry.issue("u", "lamp", "light.set", {"on": True})
    result = simulator.execute(command, now=command["issued_at"])
    assert result.result == {"accepted": True} and result.state == {"on": True}
    assert simulator.history[0].command_id == command["command_id"]
    result.state["on"] = False
    assert simulator.snapshot() == {"on": True}
    with pytest.raises(ValueError, match="replayed"):
        simulator.execute(command, now=command["issued_at"])


def test_reset_restores_state_and_optionally_history(tmp_path: Path):
    simulator = StatefulDeviceSimulator(
        "d", "11" * 32, {"read": lambda state: state.copy()}, {"n": 1}
    )
    simulator.reset({"n": 2})
    assert simulator.snapshot() == {"n": 2} and simulator.history == []
