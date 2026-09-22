from __future__ import annotations

import copy
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .devices import DeviceSimulator


@dataclass(frozen=True)
class SimulationResult:
    command_id: str
    capability: str
    result: Any
    state: dict[str, Any]


class StatefulDeviceSimulator(DeviceSimulator):
    """Protocol-faithful simulator with thread-safe state and execution history."""

    def __init__(
        self,
        device_id: str,
        secret_hex: str,
        capabilities: dict[str, Callable[..., Any]],
        initial_state: dict[str, Any] | None = None,
    ):
        self.state = copy.deepcopy(initial_state or {})
        self.history: list[SimulationResult] = []
        self._state_lock = threading.RLock()
        wrapped = {name: self._wrap(handler) for name, handler in capabilities.items()}
        super().__init__(device_id, secret_hex, wrapped)

    def _wrap(self, handler: Callable[..., Any]) -> Callable[..., Any]:
        def execute(**arguments: Any) -> Any:
            with self._state_lock:
                return handler(self.state, **arguments)

        return execute

    def execute(self, envelope: dict[str, Any], *, now: int | None = None) -> SimulationResult:
        result = super().execute(envelope, now=now)
        record = SimulationResult(
            str(envelope["command_id"]), str(envelope["capability"]), result, self.snapshot()
        )
        with self._state_lock:
            self.history.append(record)
        return record

    def snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return copy.deepcopy(self.state)

    def reset(self, state: dict[str, Any] | None = None, *, clear_history: bool = True) -> None:
        with self._state_lock:
            self.state.clear()
            self.state.update(copy.deepcopy(state or {}))
            if clear_history:
                self.history.clear()
