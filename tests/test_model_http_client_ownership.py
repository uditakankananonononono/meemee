"""Model wrappers borrow injected HTTP clients; constructed clients are owned."""
import httpx
import pytest

from meemee.config import Settings
from meemee.llm import OpenAICompatibleModel
from meemee.model_profiles import ModelCatalog, RoutedModel


@pytest.mark.parametrize('routed', [False, True])
async def test_model_close_preserves_injected_actual_http_client(routed):
    released = []
    class Transport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, text='usable')
        async def aclose(self):
            released.append(True)
    client = httpx.AsyncClient(transport=Transport())
    model = (RoutedModel(ModelCatalog.from_settings(Settings(_env_file=None)), client=client) if routed else
             OpenAICompatibleModel('http://unused', 'model', 'key', client=client))
    if routed:
        model._model_for(model.catalog.profiles['local'])
    try:
        await model.aclose()
        assert not client.is_closed and released == []
        assert (await client.get('http://unused')).text == 'usable'
    finally:
        await client.aclose()


async def test_constructed_model_still_releases_underlying_http_transport():
    released = []
    class Transport(httpx.AsyncBaseTransport):
        async def aclose(self):
            released.append(True)
    model = OpenAICompatibleModel('http://unused', 'model', 'key')
    await model.client._transport.aclose()
    model.client._transport = Transport()
    await model.aclose()
    assert model.client.is_closed and released == [True]


async def test_routed_injected_http_does_not_skip_other_cached_model_cleanup():
    events = []
    class Other:
        async def aclose(self):
            events.append('other')
    client = httpx.AsyncClient()
    model = RoutedModel(ModelCatalog.from_settings(Settings(_env_file=None)), client=client)
    model._clients['custom'] = Other()
    try:
        await model.aclose()
        assert events == ['other'] and not client.is_closed
    finally:
        await client.aclose()
