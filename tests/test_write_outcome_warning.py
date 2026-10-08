import asyncio

import pytest
from pydantic import BaseModel

from meemee.tools.base import Tool, ToolRegistry
from meemee.types import Risk


class Args(BaseModel):
 value:int

@pytest.mark.parametrize('error',[ValueError,OSError,TypeError])
def test_actual_write_then_error_warns_without_replay(tmp_path,error):
 calls=[]
 class Action(Tool):
  name='write';description='test';risk=Risk.WRITE;arguments_model=Args
  async def run(self,args):
   calls.append(args.value);(tmp_path/'effect').write_text(str(args.value));raise error('after write')
 r=ToolRegistry();r.register(Action())
 result=asyncio.run(r.execute('write',{'value':42},owner_id='owner'))
 assert not result.ok and 'outcome unknown' in result.error
 assert calls==[42] and (tmp_path/'effect').read_text()=='42'

def test_prevalidation_refusal_has_no_unknown_effect():
 class Action(Tool):
  name='write';description='test';risk=Risk.WRITE;arguments_model=Args
  async def run(self,args):raise AssertionError('should not dispatch')
 r=ToolRegistry();r.register(Action())
 result=asyncio.run(r.execute('write',{},owner_id='owner'))
 assert not result.ok and 'outcome unknown' not in result.error
