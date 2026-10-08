import asyncio,pytest
from meemee.tools.base import ToolRegistry

def tool(root):
 from meemee.tools.text_search import SearchText
 return SearchText(root)

def test_real_registry_literal_matches_and_line_numbers(tmp_path):
 (tmp_path/'file').write_text('é needle\nother\nneedle needle\n')
 r=ToolRegistry();r.register(tool(tmp_path))
 result=asyncio.run(r.execute('workspace.search_text',{'path':'file','query':'needle','max_matches':1},owner_id='owner'))
 assert result.ok and result.content['matches']==[{'line':1,'text':'é needle'}]
 assert result.content['truncated'] and result.content['matched_lines']==2

def test_actual_caps_and_path_refusal(tmp_path):
 (tmp_path/'file').write_text('needle'*100)
 t=tool(tmp_path)
 with pytest.raises(ValueError,match='byte cap'):asyncio.run(t.run(t.arguments_model(path='file',query='needle',max_bytes=20)))
 with pytest.raises(ValueError,match='escapes'):asyncio.run(t.run(t.arguments_model(path='../secret',query='x')))

def test_literal_not_regex_and_preview_bound(tmp_path):
 (tmp_path/'file').write_text('x'*10000+'.*\n')
 t=tool(tmp_path);r=asyncio.run(t.run(t.arguments_model(path='file',query='.*',preview_chars=20)))
 assert r['matches']==[{'line':1,'text':'x'*20}] and r['preview_truncated']
 assert asyncio.run(t.run(t.arguments_model(path='file',query='no-match')))['matches']==[]

@pytest.mark.asyncio
async def test_real_runtime_search_registration(tmp_path):
 from meemee.runtime import build_agent_async
 from meemee.config import Settings
 (tmp_path/'file').write_text('needle')
 agent=await build_agent_async(Settings(_env_file=None,data_dir=tmp_path/'data',workspace=tmp_path),include_delegation=False)
 try:
  result=await agent.tools.execute('workspace.search_text',{'path':'file','query':'needle'},owner_id='owner')
  assert result.ok and result.content['matched_lines']==1
 finally:await agent.aclose()
