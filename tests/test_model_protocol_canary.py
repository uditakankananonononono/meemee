"""HTTP protocol failures must use the bounded model failure path."""
import httpx
import pytest

from meemee.llm import ModelError, OpenAICompatibleModel


@pytest.mark.parametrize('method', ['chat', 'decide'])
async def test_remote_protocol_failure_maps_to_model_error(method):
    def broken(request):
        raise httpx.RemoteProtocolError('fixture premature disconnect')

    async with httpx.AsyncClient(transport=httpx.MockTransport(broken)) as client:
        model = OpenAICompatibleModel('https://fixture.invalid/v1','fixture','fixture',max_attempts=1,client=client)
        with pytest.raises(ModelError, match='1 attempts'):
            await getattr(model, method)([{'role':'user','content':'fixture'}])
