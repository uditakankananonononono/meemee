import asyncio
import pytest
from meemee.tools.filesystem import ReadFile,PathArgs
from meemee.tools.base import ToolRegistry

def test_real_registry_bounded_read(tmp_path):
 (tmp_path/'large').write_bytes(b'x'*10000)
 r=ToolRegistry();r.register(ReadFile(tmp_path))
 result=asyncio.run(r.execute('workspace.read_file',{'path':'large','max_bytes':101},owner_id='owner'))
 assert not result.ok and 'byte cap' in result.error

def test_success_and_invalid_cap(tmp_path):
 (tmp_path/'file').write_text('é')
 args=ReadFile(tmp_path).arguments_model(path='file',max_bytes=2)
 result=asyncio.run(ReadFile(tmp_path).run(args));assert result['content']=='é' and result['bytes']==2
 for cap in [0,-1,True,2.5,1000001]:
  with pytest.raises(ValueError):ReadFile.arguments_model(path='file',max_bytes=cap)

def test_default_cap_and_binary_refusal(tmp_path):
 (tmp_path/'file').write_bytes(b'x'*1000001)
 with pytest.raises(ValueError,match='byte cap'):asyncio.run(ReadFile(tmp_path).run(ReadFile.arguments_model(path='file')))
 (tmp_path/'file').write_bytes(b'\xff')
 with pytest.raises(ValueError):asyncio.run(ReadFile(tmp_path).run(ReadFile.arguments_model(path='file')))

def test_posix_fifo_refuses_without_waiting_for_writer(tmp_path):
 import os,subprocess,sys
 if not hasattr(os,'mkfifo'):pytest.skip('POSIX FIFO only')
 os.mkfifo(tmp_path/'fifo')
 script='import asyncio;from pathlib import Path;from meemee.tools.filesystem import ReadFile;from meemee.tools.base import ToolRegistry;r=ToolRegistry();r.register(ReadFile(Path('+repr(str(tmp_path))+')));v=asyncio.run(r.execute("workspace.read_file",{"path":"fifo"},owner_id="owner"));assert not v.ok and "regular" in v.error'
 p=subprocess.run([sys.executable,'-c',script],capture_output=True,text=True,timeout=3)
 assert p.returncode==0,p.stderr
