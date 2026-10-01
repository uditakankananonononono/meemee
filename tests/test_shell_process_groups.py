"""Integration tests using native ShellCommand and real Python subprocesses."""

import asyncio
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from meemee.tools.shell import ShellArgs, ShellCommand

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process groups required")

# Every node records its group and heartbeat. The root has two generations below
# it, exercising inherited pipes as well as descendants that ignore SIGTERM.
TREE = r"""
import os, pathlib, signal, subprocess, sys, time
name, depth, ignore = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
if ignore:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
pathlib.Path(name + '.pid').write_text(str(os.getpid()) + ' ' + str(os.getpgrp()))
if depth:
    subprocess.Popen([sys.executable, __file__, name + '-child', str(depth - 1), str(ignore)])
while True:
    with open(name + '.beat', 'a') as f:
        f.write('x')
    time.sleep(0.02)
"""


def tool(root):
    return ShellCommand(root, {Path(sys.executable).name})


def command(root, name="root", timeout=10, ignore=False):
    script = root / "tree.py"
    script.write_text(TREE)
    return ShellArgs(
        argv=[sys.executable, str(script), name, "2", str(int(ignore))], timeout_seconds=timeout
    )


async def ready(root, name="root"):
    deadline = time.monotonic() + 5
    files = [root / (name + suffix + ".beat") for suffix in ("", "-child", "-child-child")]
    while not all(path.exists() and path.stat().st_size > 0 for path in files):
        assert time.monotonic() < deadline, "tree did not start"
        await asyncio.sleep(0.01)
    return files


async def stopped(files):
    before = [path.stat().st_size for path in files]
    await asyncio.sleep(0.15)
    assert [path.stat().st_size for path in files] == before


def emergency_cleanup(root):
    # Test-owned PID files only, never kill the runner's group.
    for path in root.glob("*.pid"):
        pid, pgid = map(int, path.read_text().split())
        if pgid != os.getpgrp():
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.asyncio
@pytest.mark.parametrize("ignore", [False, True], ids=["term", "ignore-term"])
async def test_cancel_stops_child_and_grandchild(tmp_path, ignore):
    task = asyncio.create_task(tool(tmp_path).run(command(tmp_path, ignore=ignore)))
    try:
        files = await ready(tmp_path)
        assert all(int(p.read_text().split()[1]) != os.getpgrp() for p in tmp_path.glob("*.pid"))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        await stopped(files)
    finally:
        emergency_cleanup(tmp_path)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_simultaneous_commands_have_separate_groups(tmp_path):
    first = asyncio.create_task(tool(tmp_path).run(command(tmp_path, "first")))
    second = asyncio.create_task(tool(tmp_path).run(command(tmp_path, "second")))
    try:
        dead = await ready(tmp_path, "first")
        live = await ready(tmp_path, "second")
        assert (tmp_path / "first.pid").read_text().split()[1] != (
            tmp_path / "second.pid"
        ).read_text().split()[1]
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(first, 3)
        before = [p.stat().st_size for p in live]
        await stopped(dead)
        assert all(p.stat().st_size > size for p, size in zip(live, before))
        assert not second.done()
    finally:
        for task in (first, second):
            task.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
        emergency_cleanup(tmp_path)


@pytest.mark.asyncio
async def test_timeout_stops_owned_group(tmp_path):
    task = asyncio.create_task(tool(tmp_path).run(command(tmp_path, timeout=0.5, ignore=True)))
    try:
        files = await ready(tmp_path)
        with pytest.raises(ValueError, match="command timed out after 0.5s"):
            await asyncio.wait_for(task, 3)
        await stopped(files)
    finally:
        emergency_cleanup(tmp_path)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_during_spawn_recovers_real_process(tmp_path, monkeypatch):
    original = asyncio.create_subprocess_exec
    spawned, release = asyncio.Event(), asyncio.Event()
    processes = []

    async def delayed_handle(*args, **kwargs):
        # No substitute Process: create a native process, hold only its delivery.
        process = await original(*args, **kwargs)
        processes.append(process)
        spawned.set()
        await release.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_handle)
    task = asyncio.create_task(tool(tmp_path).run(command(tmp_path)))
    try:
        await asyncio.wait_for(spawned.wait(), 5)
        files = await ready(tmp_path)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()  # repeated cancellation must not interrupt ownership cleanup
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        assert processes[0].returncode is not None
        await stopped(files)
    finally:
        release.set()
        emergency_cleanup(tmp_path)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_repeated_cancel_during_grace_still_hard_kills(tmp_path):
    task = asyncio.create_task(tool(tmp_path).run(command(tmp_path, ignore=True)))
    try:
        files = await ready(tmp_path)
        task.cancel()
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        await stopped(files)
    finally:
        emergency_cleanup(tmp_path)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_normal_completion_and_stdout_stderr_limits(tmp_path):
    result = await tool(tmp_path).run(
        ShellArgs(
            argv=[sys.executable, "-c", "import sys; print('ok'); print('err', file=sys.stderr)"]
        )
    )
    assert result == {"exit_code": 0, "stdout": "ok\n", "stderr": "err\n", "truncated": False}
    cap = 200_000
    result = await tool(tmp_path).run(
        ShellArgs(
            argv=[
                sys.executable,
                "-c",
                f"import sys; sys.stdout.write('a'*{cap + 1}); sys.stderr.write('b'*{cap + 1})",
            ]
        )
    )
    assert result == {"exit_code": 0, "stdout": "a" * cap, "stderr": "b" * cap, "truncated": True}


@pytest.mark.asyncio
async def test_spawn_error_propagates_without_cleanup(tmp_path):
    with pytest.raises(FileNotFoundError):
        await ShellCommand(tmp_path, {"missing-command"}).run(
            ShellArgs(argv=[str(tmp_path / "missing-command")])
        )


@pytest.mark.asyncio
async def test_timeout_after_leader_exits_still_stops_children(tmp_path):
    script = tmp_path / "tree.py"
    script.write_text(TREE)
    code = "import subprocess,sys; subprocess.Popen([sys.executable,'tree.py','root','2','1'])"
    task = asyncio.create_task(
        tool(tmp_path).run(ShellArgs(argv=[sys.executable, "-c", code], timeout_seconds=0.5))
    )
    try:
        files = await ready(tmp_path)
        with pytest.raises(ValueError, match="timed out"):
            await asyncio.wait_for(task, 3)
        await stopped(files)
    finally:
        emergency_cleanup(tmp_path)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_escaped_descendant_with_pipes_does_not_block_cleanup(tmp_path):
    child = "import pathlib,time; pathlib.Path('escaped.pid').write_text(str(__import__('os').getpid())+' '+str(__import__('os').getpgrp())); time.sleep(30)"
    parent = (
        "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',"
        f"{child!r}],start_new_session=True); time.sleep(30)"
    )
    task = asyncio.create_task(tool(tmp_path).run(ShellArgs(argv=[sys.executable, "-c", parent])))
    try:
        deadline = time.monotonic() + 5
        while not (tmp_path / "escaped.pid").exists():
            assert time.monotonic() < deadline
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        # Explicit limit: escaped process remains alive and is test-owned cleanup,
        # not something ShellCommand is authorized to signal.
        pid = int((tmp_path / "escaped.pid").read_text().split()[0])
        os.kill(pid, 0)
    finally:
        emergency_cleanup(tmp_path)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_registry_cancel_event_stops_native_group(tmp_path):
    from meemee.tools.base import ToolRegistry

    registry = ToolRegistry()
    registry.register(tool(tmp_path))
    cancel = asyncio.Event()
    task = asyncio.create_task(
        registry.execute("workspace.run_command", command(tmp_path).model_dump(), cancel=cancel)
    )
    try:
        files = await ready(tmp_path)
        cancel.set()
        result = await asyncio.wait_for(task, 3)
        assert not result.ok and result.error == "tool cancelled"
        await stopped(files)
    finally:
        emergency_cleanup(tmp_path)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
