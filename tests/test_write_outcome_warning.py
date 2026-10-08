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

@pytest.mark.asyncio
async def test_missing_run_parameter_refuses_before_dispatch():
    calls=[]
    class Action(Tool):
        name='write';description='signature';risk=Risk.WRITE;arguments_model=Args
        async def run(self,args,required):
            calls.append(required)
    registry=ToolRegistry();registry.register(Action())
    result=await registry.execute('write',{'value':42},owner_id='owner')
    assert not result.ok and 'required' in result.error
    assert 'outcome unknown' not in result.error and calls==[]

@pytest.mark.asyncio
async def test_body_typeerror_still_warns_once():
    calls=[]
    class Action(Tool):
        name='write';description='body';risk=Risk.WRITE;arguments_model=Args
        async def run(self,args):
            calls.append(args.value)
            raise TypeError('inside body')
    registry=ToolRegistry();registry.register(Action())
    result=await registry.execute('write',{'value':42},owner_id='owner')
    assert not result.ok and 'inside body' in result.error
    assert 'outcome unknown' in result.error and calls==[42]
