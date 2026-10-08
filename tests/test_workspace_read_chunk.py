import asyncio
import base64

import pytest


def tool(root):
 from meemee.tools.file_chunk import ReadChunk
 return ReadChunk(root)

def test_actual_binary_pagination(tmp_path):
 data=b'\xff'+b'x'*1000001;(tmp_path/'file').write_bytes(data);t=tool(tmp_path)
 r=asyncio.run(t.run(t.arguments_model(path='file',offset=1000000,max_bytes=2)))
 assert base64.b64decode(r['base64'])==data[1000000:1000002] and r['eof'] and r['next_offset']==1000002
 r=asyncio.run(t.run(t.arguments_model(path='file',max_bytes=1)))
 assert base64.b64decode(r['base64'])==b'\xff' and not r['eof']

def test_offsets_and_caps(tmp_path):
 t=tool(tmp_path);(tmp_path/'file').write_bytes(b'a')
 for offset in [-1,True,1.5]:
  with pytest.raises(ValueError):t.arguments_model(path='file',offset=offset)
 r=asyncio.run(t.run(t.arguments_model(path='file',offset=10,max_bytes=10)))
 assert r['base64']=='' and r['eof']
 with pytest.raises(ValueError,match='escapes'):asyncio.run(t.run(t.arguments_model(path='../x')))

@pytest.mark.asyncio
async def test_actual_runtime_registration(tmp_path):
 from meemee.config import Settings
 from meemee.runtime import build_agent_async
 (tmp_path/'file').write_bytes(b'hello')
 agent=await build_agent_async(Settings(_env_file=None,data_dir=tmp_path/'data',workspace=tmp_path),include_delegation=False)
 try:
  r=await agent.tools.execute('workspace.read_chunk',{'path':'file','max_bytes':2},owner_id='owner')
  assert r.ok and base64.b64decode(r.content['base64'])==b'he' and not r.content['eof']
 finally:await agent.aclose()
