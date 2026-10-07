"""Outer async cancellation must not leave an isolated process running."""
import asyncio
import os
import threading
import time

import pytest
from pydantic import BaseModel

from meemee.isolation import IsolationPolicy, run_isolated


class Args(BaseModel):
    pid_file: str


class SlowProcess:
    def run(self, arguments):
        from pathlib import Path
        Path(arguments.pid_file).write_text(str(os.getpid()))
        time.sleep(5)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@pytest.mark.asyncio
async def test_outer_cancel_waits_for_isolated_child_reaping(tmp_path):
    pid_file = tmp_path / 'pid'
    flag = threading.Event()
    task = asyncio.create_task(run_isolated(SlowProcess(), Args(pid_file=str(pid_file)),
        IsolationPolicy(timeout_s=10, grace_s=0.1), flag))
    try:
        for _ in range(200):
            if pid_file.exists() and pid_file.read_text():
                break
            await asyncio.sleep(0.01)
        assert pid_file.exists(), 'process did not start'
        pid = int(pid_file.read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not alive(pid), 'cancelled wrapper returned with live child'
    finally:
        # Cleanup original-source reproduction, not part of correctness assertion.
        flag.set()
        await asyncio.sleep(0.25)
