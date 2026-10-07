"""Real subprocess regressions for bounded output and executable identity."""
import sys
import tracemalloc

import pytest

from meemee.tools.shell import ShellArgs, ShellCommand


@pytest.mark.asyncio
async def test_allowlist_cannot_run_workspace_impostor(tmp_path):
    impostor = tmp_path / 'printf'
    impostor.write_text('#!/bin/sh\necho bypassed\n')
    impostor.chmod(0o755)
    with pytest.raises(ValueError, match='allowlisted|executable'):
        await ShellCommand(tmp_path, {'printf'}).run(ShellArgs(argv=[str(impostor)]))


@pytest.mark.asyncio
async def test_output_capture_has_streaming_memory_bound(tmp_path):
    tool = ShellCommand(tmp_path, {sys.executable.split('/')[-1]})
    tracemalloc.start()
    try:
        result = await tool.run(ShellArgs(argv=[sys.executable, '-c',
            "import os; [os.write(1, b'x'*65536) for _ in range(256)]" ]))
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert result['exit_code'] == 0
    assert result['truncated'] is True and len(result['stdout']) == 200_000
    assert peak < 4_000_000, f'captured unlimited output: peak={peak}'


@pytest.mark.asyncio
async def test_both_pipes_drained_after_cap_without_deadlock(tmp_path):
    result = await ShellCommand(tmp_path, {sys.executable.split('/')[-1]}).run(
        ShellArgs(argv=[sys.executable, '-c', "import os; [ (os.write(1,b'x'*65536),os.write(2,b'y'*65536)) for _ in range(32)]"]))
    assert result['exit_code'] == 0 and result['truncated']
    assert len(result['stdout']) == len(result['stderr']) == 200_000


@pytest.mark.asyncio
async def test_timeout_cleans_child_with_inherited_pipes(tmp_path):
    import asyncio
    import os
    import time
    if os.name != 'posix':
        pytest.skip('POSIX process-group cleanup')
    marker = tmp_path / 'leaked'
    child = f"import time,pathlib; time.sleep(0.5); pathlib.Path({str(marker)!r}).write_text('leaked'); time.sleep(5)"
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(5)"
    began = time.monotonic()
    with pytest.raises(ValueError, match='timed out'):
        await ShellCommand(tmp_path, {sys.executable.split('/')[-1]}).run(
            ShellArgs(argv=[sys.executable, '-c', parent], timeout_seconds=0.2))
    assert time.monotonic() - began < 2
    await asyncio.sleep(0.6)
    assert not marker.exists()
