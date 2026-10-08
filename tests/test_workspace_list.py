import asyncio,pytest
from meemee.tools.base import ToolRegistry

def tool(root):
 from meemee.tools.filesystem import ListFiles
 return ListFiles(root)

def test_actual_registry_lists_sorted_metadata(tmp_path):
 (tmp_path/'b').write_text('hello');(tmp_path/'a').mkdir();(tmp_path/'.hidden').write_text('private')
 registry=ToolRegistry();registry.register(tool(tmp_path))
 r=asyncio.run(registry.execute('workspace.list_files',{'path':'.','limit':1},owner_id='owner'))
 assert r.ok and r.content['entries']==[{'name':'a','kind':'directory'}] and r.content['truncated']
 r=asyncio.run(registry.execute('workspace.list_files',{'path':'.','include_hidden':True},owner_id='owner'))
 assert [x['name'] for x in r.content['entries']]==['.hidden','a','b']

def test_scan_cap_and_escape_refusal(tmp_path):
 t=tool(tmp_path)
 for i in range(4):(tmp_path/str(i)).touch()
 with pytest.raises(ValueError,match='scan cap'):asyncio.run(t.run(t.arguments_model(path='.',scan_limit=3)))
 with pytest.raises(ValueError,match='escapes'):asyncio.run(t.run(t.arguments_model(path='..')))

def test_symlink_not_followed(tmp_path):
 (tmp_path/'link').symlink_to('/does-not-exist')
 t=tool(tmp_path);r=asyncio.run(t.run(t.arguments_model(path='.')))
 assert r['entries']==[{'name':'link','kind':'symlink'}]

@pytest.mark.asyncio
async def test_actual_async_runtime_registration(tmp_path):
 from meemee.runtime import build_agent_async
 from meemee.config import Settings
 agent=await build_agent_async(Settings(_env_file=None,data_dir=tmp_path/'data',workspace=tmp_path),include_delegation=False)
 try:
  result=await agent.tools.execute('workspace.list_files',{'path':'.'},owner_id='owner')
  assert result.ok and any(x['name']=='data' for x in result.content['entries'])
 finally:await agent.aclose()
