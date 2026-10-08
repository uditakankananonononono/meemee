import subprocess
import sys

import pytest

from meemee.tools.git import GitInspect, GitReadArgs


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['diff', 'status'])
@pytest.mark.parametrize('name', ['unsafe', 'with.dot'])
async def test_inspection_does_not_execute_clean_filter(tmp_path, operation, name):
    def git(*args):
        return subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True)

    git('init')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    (tmp_path / '.gitattributes').write_text(f'tracked.txt filter={name}\n')
    (tmp_path / 'tracked.txt').write_text('old\n')
    git('add', '.')
    git('commit', '-m', 'base')
    marker = tmp_path / 'filter-executed'
    helper = tmp_path / 'filter.py'
    helper.write_text(
        'from pathlib import Path\n'
        f'Path({str(marker)!r}).write_text("executed")\n'
        'import sys\n'
        'sys.stdout.buffer.write(sys.stdin.buffer.read())\n'
    )
    git('config', f'filter.{name}.clean', sys.executable + ' ' + str(helper))
    git('config', f'filter.{name}.required', 'true')
    (tmp_path / 'tracked.txt').write_text('new\n')
    result = await GitInspect(tmp_path).run(GitReadArgs(operation=operation))
    assert 'tracked.txt' in result['stdout']
    if operation == 'diff':
        assert '-old' in result['stdout'] and '+new' in result['stdout']
    assert not marker.exists(), 'READ inspection executed configured clean filter'


@pytest.mark.asyncio
async def test_inspection_disables_process_filter(tmp_path):
    def git(*args):
        return subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True)

    git('init')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    (tmp_path / '.gitattributes').write_text('tracked.txt filter=process-only\n')
    (tmp_path / 'tracked.txt').write_text('old\n')
    git('add', '.')
    git('commit', '-m', 'base')
    marker = tmp_path / 'process-executed'
    helper = tmp_path / 'process.py'
    helper.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("ran")\n')
    git('config', 'filter.process-only.process', sys.executable + ' ' + str(helper))
    git('config', 'filter.process-only.required', 'true')
    (tmp_path / 'tracked.txt').write_text('new\n')
    result = await GitInspect(tmp_path).run(GitReadArgs(operation='diff'))
    assert '-old' in result['stdout'] and '+new' in result['stdout']
    assert not marker.exists()


@pytest.mark.asyncio
async def test_incomplete_config_discovery_refuses_inspection(tmp_path):
    class TruncatedConfig(GitInspect):
        async def git(self, *args, **kwargs):
            assert args[0] == 'config', 'inspection must not proceed after truncated discovery'
            return {'stdout': 'filter.unsafe.clean\0', 'truncated': True}

    with pytest.raises(ValueError, match='discovery limit'):
        await TruncatedConfig(tmp_path).run(GitReadArgs(operation='diff'))
