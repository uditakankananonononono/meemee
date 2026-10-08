import asyncio,hashlib
from meemee.tools.filesystem import WriteFile
from meemee.tools.base import ToolRegistry

def test_real_registry_stale_edit_preserves_file(tmp_path):
 path=tmp_path/'file';path.write_text('new user edit')
 r=ToolRegistry();r.register(WriteFile(tmp_path))
 result=asyncio.run(r.execute('workspace.write_file',{'path':'file','content':'agent replacement','expected_sha256':hashlib.sha256(b'old').hexdigest()},owner_id='owner'))
 assert not result.ok and 'changed' in result.error and path.read_text()=='new user edit'

def test_correct_expected_hash_writes(tmp_path):
 path=tmp_path/'file';path.write_text('old')
 tool=WriteFile(tmp_path)
 result=asyncio.run(tool.run(tool.arguments_model(path='file',content='new',expected_sha256=hashlib.sha256(b'old').hexdigest())))
 assert path.read_text()=='new' and result['sha256']==hashlib.sha256(b'new').hexdigest()

def test_missing_precondition_does_not_create_parent(tmp_path):
 import pytest
 tool=WriteFile(tmp_path)
 with pytest.raises(ValueError,match='missing'):
  asyncio.run(tool.run(tool.arguments_model(path='absent/file',content='new',expected_sha256='0'*64)))
 assert not (tmp_path/'absent').exists()

def test_invalid_digest_schema_refuses():
 import pytest
 for digest in ['bad','A'*64,'0'*63]:
  with pytest.raises(ValueError):WriteFile.arguments_model(path='file',content='new',expected_sha256=digest)
