import asyncio,subprocess
from meemee.tools.git import GitInspect,GitReadArgs

def test_actual_git_diff_budget(tmp_path):
 def git(*args):return subprocess.run(['git',*args],cwd=tmp_path,check=True,capture_output=True)
 git('init');git('config','user.email','fixture@example.invalid');git('config','user.name','Fixture')
 (tmp_path/'file').write_text('old\n');git('add','.');git('commit','-m','base')
 (tmp_path/'file').write_text('x'*10000+'\n')
 r=asyncio.run(GitInspect(tmp_path).run(GitReadArgs(operation='diff',max_output_bytes=101)))
 assert r['truncated'] and len(r['stdout'].encode())<=101 and r['exit_code']==0

def test_log_remains_runnable(tmp_path):
 subprocess.run(['git','init'],cwd=tmp_path,check=True,capture_output=True)
 r=asyncio.run(GitInspect(tmp_path).run(GitReadArgs(operation='status',max_output_bytes=1000)))
 assert r['exit_code']==0 and not r['truncated']

def test_actual_git_child_timeout_cleanup(tmp_path):
 import pytest,time
 from meemee.tools.git import GitBase
 subprocess.run(['git','init'],cwd=tmp_path,check=True,capture_output=True)
 subprocess.run(['git','config','alias.slow','!sleep 10'],cwd=tmp_path,check=True)
 start=time.monotonic()
 with pytest.raises(ValueError,match='timed out'):
  asyncio.run(GitInspect(tmp_path).git('slow',timeout=.1))
 assert time.monotonic()-start<3

def test_git_budget_controls_are_strict():
 import pytest
 for value in [True,0,-1,1.5,1000001]:
  with pytest.raises(ValueError):GitReadArgs(operation='diff',max_output_bytes=value)
