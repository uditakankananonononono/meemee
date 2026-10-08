"""Synchronous compatibility for async resource composition."""
import asyncio
from collections.abc import Callable, Coroutine
from typing import Any


def run_composition(factory: Callable[[], Coroutine[Any, Any, Any]]) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())
    raise RuntimeError('synchronous composition cannot run inside an event loop; await the async factory')
