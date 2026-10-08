import asyncio
import subprocess
import sys

from meemee.tools.git import GitInspect, GitReadArgs


def test_actual_inspection_does_not_execute_textconv(tmp_path):
    def git(*args):
        return subprocess.run(['git', *args], cwd=tmp_path, check=True, capture_output=True)
    git('init')
    git('config', 'user.name', 'Fixture')
    git('config', 'user.email', 'fixture@example.invalid')
    marker=tmp_path/'effect'
    helper=tmp_path/'converter.py'
    helper.write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("ran")\nprint("converted")\n')
    git('config','diff.unsafe.textconv',sys.executable+' '+str(helper))
    (tmp_path/'.gitattributes').write_text('file diff=unsafe\n')
    (tmp_path/'file').write_text('old\n')
    git('add','.gitattributes','file');git('commit','-m','base')
    (tmp_path/'file').write_text('new\n')
    result=asyncio.run(GitInspect(tmp_path).run(GitReadArgs(operation='diff')))
    assert not marker.exists()
    assert '-old' in result['stdout'] and '+new' in result['stdout']
