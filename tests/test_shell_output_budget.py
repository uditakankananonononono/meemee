import asyncio,sys
import pytest
from meemee.tools.shell import ShellArgs,ShellCommand

def test_actual_multibyte_budget(tmp_path):
 tool=ShellCommand(tmp_path,{sys.executable})
 args=ShellArgs(argv=[sys.executable,'-c',"import os;os.write(1,('é'*10000).encode());os.write(2,b'x'*5000)"],max_output_bytes=101)
 r=asyncio.run(tool.run(args))
 assert r['stdout_bytes']==20000 and r['stderr_bytes']==5000
 assert r['stdout_retained_bytes']==101 and r['stderr_retained_bytes']==101
 assert r['stdout_truncated_bytes']==19899 and r['stderr_truncated_bytes']==4899 and r['truncated']
 assert len(r['stdout'].encode())<=104

def test_actual_nontruncated_output_and_timeout(tmp_path):
 tool=ShellCommand(tmp_path,{sys.executable})
 r=asyncio.run(tool.run(ShellArgs(argv=[sys.executable,'-c',"print(6*7)"],max_output_bytes=100)))
 assert r['stdout']=='42\n' and r['stdout_bytes']==3 and not r['truncated']
 with pytest.raises(ValueError,match='timed out'):
  asyncio.run(tool.run(ShellArgs(argv=[sys.executable,'-c','import time;time.sleep(10)'],timeout_seconds=.1,max_output_bytes=10)))

def test_budget_validation():
 for value in [0,-1,1000001,True,1.5]:
  with pytest.raises(ValueError):ShellArgs(argv=['python3'],max_output_bytes=value)
