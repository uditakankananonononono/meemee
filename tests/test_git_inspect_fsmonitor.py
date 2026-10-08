import subprocess
import sys

import pytest

from meemee.tools.git import GitInspect, GitReadArgs


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["status", "diff"])
async def test_inspection_does_not_execute_configured_fsmonitor(tmp_path, operation):
    def git(*args):
        return subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True)

    git('init')
    (tmp_path / 'tracked.txt').write_text('content\n')
    git('add', 'tracked.txt')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    git('commit', '-m', 'base')
    (tmp_path / 'tracked.txt').write_text('changed\n')
    marker = tmp_path / 'fsmonitor-executed'
    helper = tmp_path / 'monitor.py'
    helper.write_text(
        f'#!{sys.executable}\n'
        'from pathlib import Path\n'
        f'Path({str(marker)!r}).write_text("executed")\n'
        'import sys\n'
        'sys.stdout.buffer.write(b"token\\x00/\\x00")\n'
    )
    helper.chmod(0o755)
    git('config', 'core.fsmonitor', str(helper))
    result = await GitInspect(tmp_path).run(GitReadArgs(operation=operation))
    assert 'tracked.txt' in result['stdout']
    assert not marker.exists(), 'READ inspection executed the configured fsmonitor helper'
