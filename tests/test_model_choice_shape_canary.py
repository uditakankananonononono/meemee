"""Malformed completion choices must remain explicit model failures."""
import httpx
import pytest

from meemee.llm import ModelError, OpenAICompatibleModel


@pytest.mark.parametrize('method', ['chat', 'decide'])
@pytest.mark.parametrize('choice', [None, 'invalid'])
async def test_choice_shape_is_rejected_as_model_error(method, choice):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200,json={'choices':[choice]}))) as client:
        model = OpenAICompatibleModel('https://fixture.invalid/v1','fixture','fixture',client=client)
        with pytest.raises(ModelError, match='invalid'):
            await getattr(model, method)([{'role':'user','content':'fixture'}])
