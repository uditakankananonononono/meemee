"""Provider truncation must not be presented as a complete answer/decision."""
import httpx
import pytest

from meemee.llm import ModelError, OpenAICompatibleModel


@pytest.mark.parametrize('method', ['chat', 'decide'])
async def test_length_finish_reason_is_explicit_failure(method):
    text = 'incomplete answer' if method == 'chat' else '{"thought":"fixture","final":"incomplete"}'
    payload = {'choices':[{'finish_reason':'length','message':{'content':text}}]}
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200,json=payload))) as client:
        model = OpenAICompatibleModel('https://fixture.invalid/v1','fixture','fixture',client=client)
        with pytest.raises(ModelError, match='truncated'):
            await getattr(model, method)([{'role':'user','content':'fixture'}])
