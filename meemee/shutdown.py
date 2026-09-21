from __future__ import annotations

import asyncio


class RunGate:
    """Tracks active API runs and supports bounded graceful shutdown."""

    def __init__(self):
        self._active = 0
        self._accepting = True
        self._condition = asyncio.Condition()

    @property
    def accepting(self) -> bool:
        return self._accepting

    @property
    def active(self) -> int:
        return self._active

    async def enter(self) -> None:
        async with self._condition:
            if not self._accepting:
                raise RuntimeError("server is draining")
            self._active += 1

    async def leave(self) -> None:
        async with self._condition:
            self._active -= 1
            if self._active < 0:
                raise RuntimeError("run gate underflow")
            if self._active == 0:
                self._condition.notify_all()

    async def drain(self, timeout: float) -> bool:
        async with self._condition:
            self._accepting = False
            if self._active == 0:
                return True
            try:
                await asyncio.wait_for(self._condition.wait_for(lambda: self._active == 0), timeout)
                return True
            except (TimeoutError, asyncio.TimeoutError):
                return False
